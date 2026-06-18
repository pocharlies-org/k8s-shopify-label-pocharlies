#!/usr/bin/env python3
"""
Order-edit production monitor (Skirmshop).

Runs on a CronJob every few hours for one week after the customer post-purchase
order-editing feature went live (2026-06-18). Each run:

  1. Reads the event_store for OrderEditApplied / OrderEditBalanceSettled events
     in the last LOOKBACK_HOURS, EXCLUDING the canary test order.
  2. For each REAL customer order-edit, validates the lifecycle against Shopify
     (edit committed, hold consistent with balance/payment, settle on payment)
     and against Picqer (no comment-spam regression).
  3. Health-checks the public endpoint.
  4. Reports to the ops Telegram topic: detail on real usage, alert on anomalies,
     a once-a-day heartbeat otherwise (to avoid spamming "no usage").

Self-contained: Python stdlib + `psql`. All creds come from the environment
(mounted from labels-secrets / labels-telegram-bot, mirroring the reconciler).
After MONITOR_END_DATE it is a silent no-op (delete the CronJob to stop it).
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, date

# --- config from env -------------------------------------------------------
PG_URL = os.environ["PG_URL"]
SHOP = os.environ.get("SHOPIFY_SHOP_DOMAIN", "ymimst-yh.myshopify.com")
API_KEY = os.environ["SHOPIFY_API_KEY"]
API_SECRET = os.environ["SHOPIFY_API_SECRET"]
API_VERSION = os.environ.get("SHOPIFY_API_VERSION", "2026-04")
PICQER_KEY = os.environ.get("PICQER_API_KEY", "")
PICQER_BASE = os.environ.get("PICQER_BASE_URL", "https://skirmshop.picqer.com/api/v1")
PICQER_UA = os.environ.get("PICQER_USER_AGENT", "SkirmshopLabels (info@e-dani.com)")
TG_TOKEN = os.environ["OPS_TELEGRAM_BOT_TOKEN"]
TG_CHAT = os.environ["OPS_TELEGRAM_CHAT_ID"]
TG_THREAD = os.environ.get("OPS_TELEGRAM_LABELS_THREAD_ID", "")
PUBLIC_BASE = os.environ.get(
    "ORDER_EDIT_PUBLIC_BASE", "https://track.skirmshop.es/labels/api/customer/order-edit"
)
END_DATE = date.fromisoformat(os.environ.get("MONITOR_END_DATE", "2026-06-25"))
TEST_ORDER_GID = os.environ.get("TEST_ORDER_GID", "gid://shopify/Order/7601510842709")
TEST_CUSTOMER_ID = os.environ.get("TEST_CUSTOMER_ID", "10219036574037")
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "8"))
HEARTBEAT_HOUR_UTC = int(os.environ.get("HEARTBEAT_HOUR_UTC", "6"))

now = datetime.now(timezone.utc)
problems: list[str] = []  # anomalies → force a Telegram alert
notes: list[str] = []  # informational lines


def log(msg: str) -> None:
    print(f"[order-edit-monitor] {msg}", flush=True)


def psql(query: str) -> list[list[str]]:
    out = subprocess.run(
        ["psql", PG_URL, "-At", "-F", "\x1f", "-c", query],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr.strip()[:300]}")
    rows = [line.split("\x1f") for line in out.stdout.splitlines() if line]
    return rows


def http(method: str, url: str, headers: dict, body: bytes | None = None) -> tuple[int, bytes]:
    headers = {"User-Agent": "SkirmshopOrderEditMonitor/1.0", **headers}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def gql(token: str, query: str) -> dict:
    status, raw = http(
        "POST",
        f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json",
        {"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json.dumps({"query": query}).encode(),
    )
    return json.loads(raw or b"{}")


def shopify_token() -> str:
    status, raw = http(
        "POST",
        f"https://{SHOP}/admin/oauth/access_token",
        {"Content-Type": "application/json"},
        json.dumps(
            {"client_id": API_KEY, "client_secret": API_SECRET, "grant_type": "client_credentials"}
        ).encode(),
    )
    tok = json.loads(raw or b"{}").get("access_token")
    if not tok:
        raise RuntimeError(f"client_credentials failed: {status}")
    return tok


def picqer(path: str) -> object:
    import base64

    auth = base64.b64encode(f"{PICQER_KEY}:".encode()).decode()
    status, raw = http(
        "GET",
        f"{PICQER_BASE}/{path}",
        {"Authorization": f"Basic {auth}", "User-Agent": PICQER_UA},
    )
    if status >= 400:
        raise RuntimeError(f"picqer {path} -> {status}")
    return json.loads(raw or b"null")


def telegram(text: str) -> None:
    payload = {"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if TG_THREAD:
        payload["message_thread_id"] = int(TG_THREAD)
    status, raw = http(
        "POST",
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        {"Content-Type": "application/json"},
        json.dumps(payload).encode(),
    )
    if status >= 400:
        log(f"telegram send failed {status}: {raw[:200]!r}")


def validate_order(token: str, gid: str, evs: list[dict]) -> str:
    """Return one report line (✅/⚠️) for a real order-edit; append to problems on anomaly."""
    name = gid
    applied = [e for e in evs if e["type"] == "OrderEditApplied"]
    settled = [e for e in evs if e["type"] == "OrderEditBalanceSettled"]
    try:
        d = gql(
            token,
            '{ order(id:"%s"){ name displayFulfillmentStatus displayFinancialStatus '
            "totalOutstandingSet{shopMoney{amount}} shippingLine{ code } "
            "lineItems(first:1){ nodes{ id } } } }" % gid,
        )
        o = (d.get("data") or {}).get("order")
        if not o:
            problems.append(f"order {gid} not found in Shopify (events exist)")
            return f"⚠️ {gid}: events present but order not found in Shopify"
        name = o["name"]
        outstanding = float(o["totalOutstandingSet"]["shopMoney"]["amount"])
        fin = o["displayFinancialStatus"]
        ful = o["displayFulfillmentStatus"]
        verdict = "✅"
        detail = []
        # Hold consistency
        if applied and not settled:
            if outstanding <= 0:
                verdict = "⚠️"
                detail.append("OrderEditApplied unsettled but outstanding=0")
                problems.append(f"{name}: applied(no settle) but outstanding 0 — hold may be wrong")
            else:
                detail.append(f"hold active, due {outstanding:.2f}")
        elif settled:
            if outstanding > 0:
                verdict = "⚠️"
                detail.append(f"settled but outstanding {outstanding:.2f}")
                problems.append(f"{name}: settled but still owes {outstanding:.2f}")
            else:
                detail.append("settled/paid")
        kinds = ",".join(
            sorted({(e.get("kind") or "?") for e in applied})
        )
        detail.append(f"{len(applied)} edit(s) [{kinds}] {fin}/{ful}")
        # Picqer comment-spam regression check
        if PICQER_KEY:
            try:
                numeric = gid.rsplit("/", 1)[-1]
                wso = picqer(f"webshoporders?foreign_id={numeric}")
                if isinstance(wso, list) and wso:
                    idorder = wso[0].get("idorder")
                    po = picqer(f"orders/{idorder}")
                    cc = po.get("comment_count", 0) if isinstance(po, dict) else 0
                    # bug-5 fix: ~1 comment per held edit; flag clear spam
                    if cc > len(applied) + 4:
                        verdict = "⚠️"
                        detail.append(f"Picqer comment_count={cc} (possible spam regression)")
                        problems.append(f"{name}: Picqer comment_count={cc} — check comment-spam fix")
                    else:
                        detail.append(f"picqer {po.get('status')} cc={cc}")
            except Exception as e:  # noqa: BLE001
                detail.append(f"picqer check failed: {e}")
        return f"{verdict} <b>{name}</b>: " + "; ".join(detail)
    except Exception as e:  # noqa: BLE001
        problems.append(f"{name}: validation error {e}")
        return f"⚠️ {name}: validation error {e}"


def main() -> int:
    if now.date() > END_DATE:
        log(f"past MONITOR_END_DATE {END_DATE}; no-op. Delete the CronJob to stop.")
        return 0

    real_lines: list[str] = []
    try:
        rows = psql(
            "SELECT aggregate_id, event_type, COALESCE(payload->>'kind','') "
            "FROM event_store "
            "WHERE event_type IN ('OrderEditApplied','OrderEditBalanceSettled') "
            f"AND occurred_at > now() - interval '{LOOKBACK_HOURS} hours' "
            f"AND aggregate_id <> '{TEST_ORDER_GID}' "
            "ORDER BY aggregate_id, version"
        )
        by_order: dict[str, list[dict]] = {}
        for agg, etype, kind in rows:
            by_order.setdefault(agg, []).append({"type": etype, "kind": kind})
        if by_order:
            token = shopify_token()
            for gid, evs in by_order.items():
                real_lines.append(validate_order(token, gid, evs))
        else:
            notes.append(f"No real order-edits in the last {LOOKBACK_HOURS}h.")
    except Exception as e:  # noqa: BLE001
        problems.append(f"event_store/validation step failed: {e}")

    # Endpoint health
    try:
        status, _ = http("OPTIONS", f"{PUBLIC_BASE}/eligibility", {})
        if status != 204:
            problems.append(f"endpoint OPTIONS eligibility -> {status} (expected 204)")
        else:
            notes.append("endpoint healthy (OPTIONS 204)")
    except Exception as e:  # noqa: BLE001
        problems.append(f"endpoint health check failed: {e}")

    # Observability: always log the full picture to stdout (Telegram may be
    # throttled to heartbeats, but the logs must always show what was found).
    is_heartbeat = now.hour == HEARTBEAT_HOUR_UTC
    log(f"summary: real_edits={len(real_lines)} anomalies={len(problems)} heartbeat={is_heartbeat}")
    for p in problems:
        log("ANOMALY: " + p)
    for n in notes:
        log("note: " + n)
    for rl in real_lines:
        log("edit: " + rl)

    # Decide whether to notify
    if real_lines or problems or is_heartbeat:
        head = "🩺 <b>Order-edit monitor</b>"
        if problems:
            head = "🚨 <b>Order-edit monitor — ANOMALY</b>"
        parts = [head, f"<i>{now:%Y-%m-%d %H:%M} UTC · last {LOOKBACK_HOURS}h</i>"]
        if real_lines:
            parts.append("\n<b>Real customer edits:</b>\n" + "\n".join(real_lines))
        if problems:
            parts.append("\n<b>⚠️ Anomalies:</b>\n" + "\n".join(f"• {p}" for p in problems))
        if not real_lines and not problems:
            parts.append("\n" + " · ".join(notes) + "\n<i>(daily heartbeat)</i>")
        telegram("\n".join(parts))
        log("notified telegram")
    else:
        log("silent run: " + " · ".join(notes))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"fatal: {e}")
        # best-effort alert on a fatal monitor failure
        try:
            telegram(f"🚨 <b>Order-edit monitor crashed</b>\n<code>{str(e)[:300]}</code>")
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)
