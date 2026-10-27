#!/usr/bin/env python3
"""Recargo de combustible de CEX y Correos matriz desde Gmail → ConfigMaps de GitOps (SKIRM-312).

Determinista, sin LLM. Lo corre `.github/workflows/fuel-sync.yml`; el workflow commitea si cambia algo.

- CEX: email «Actualización del suplemento de combustible» de no-reply@correosexpress.com (~día 5),
  «Tasa regulada: 3.094 %». Vigente desde la fecha del email.
- Correos matriz: no manda email del recargo; sale en la factura mensual de facturas.clientes@correos.com
  (PDF, línea «Supl. combustible 9,4 %»). Vigente desde el día 1 del mes siguiente a la fecha de factura.

Sin dato nuevo no escribe nada; un patrón que no casa o un % fuera de rango sale con error (el watchdog
labels-fuel-freshness avisa si el mes queda sin aplicar). Nunca retrocede la fecha vigente.

Credencial: GMAIL_TOKEN_JSON = authorized_user de Google (client_id, client_secret, refresh_token) con
solo gmail.readonly sobre info@e-dani.com (info@skirmshop.es es su alias).
"""
import base64
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
REPO = Path(__file__).resolve().parent.parent
CEX_FILE = REPO / "k8s/cex-config.yaml"
CORREOS_FILE = REPO / "k8s/correos-surcharges.yaml"
CEX_QUERY = 'from:no-reply@correosexpress.com subject:"suplemento de combustible" newer_than:45d'
CORREOS_QUERY = "from:facturas.clientes@correos.com has:attachment newer_than:45d"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def pct(raw: str) -> float:
    value = float(raw.replace(",", "."))
    if not 0 < value < 30:
        raise ValueError(f"recargo fuera de rango: {raw} %")
    return value


def parse_cex(text: str) -> tuple[float, str]:
    """Texto del email de CEX → (%, mes al que corresponde la tasa)."""
    text = re.sub(r"[*_]", "", text)
    m = re.search(r"Tasa regulada:\s*([0-9]+(?:[.,][0-9]+)?)\s*%", text)
    if not m:
        raise ValueError("CEX: el email no trae «Tasa regulada: N %»")
    mes = re.search(r"aplicable al mes de\s+(\w+)", text)
    return pct(m.group(1)), (mes.group(1).lower() if mes else "?")


def parse_correos(pdf_text: str) -> float:
    """Texto (pdftotext -layout) de la factura de Correos → %."""
    m = re.search(r"Supl(?:\.|emento)\s*combustible\s+([0-9]+(?:,[0-9]+)?)\s*%", pdf_text)
    if not m:
        raise ValueError("Correos: la factura no trae «Supl. combustible N %»")
    return pct(m.group(1))


def correos_effective_from(body: str) -> dt.date:
    """«factura emitida con fecha 30/09/26» → 2026-10-01."""
    m = re.search(r"con fecha\s+(\d{2})/(\d{2})/(\d{2,4})", body)
    if not m:
        raise ValueError("Correos: el email no trae la fecha de la factura")
    d, mo, y = (int(g) for g in m.groups())
    y += 2000 if y < 100 else 0
    return dt.date(y + mo // 12, mo % 12 + 1, 1)


def current(text: str) -> dict:
    """Los tres campos fuel del primer JSON embebido que los tenga."""
    out = {}
    for key in ("fuel_surcharge_pct", "fuel_surcharge_effective_from", "fuel_surcharge_source"):
        m = re.search(rf'"{key}":\s*("(?:[^"\\]|\\.)*"|[0-9.]+)', text)
        if not m:
            raise ValueError(f"no encuentro {key}")
        out[key] = json.loads(m.group(1))
    return out


def apply(text: str, value: float, effective: dt.date, source: str, today: dt.date) -> str:
    """Reescribe solo los campos fuel y sus anotaciones; el resto del manifiesto queda igual."""
    subs = [
        (r'("fuel_surcharge_pct":\s*)[0-9.]+', rf"\g<1>{value:g}"),
        (r'("fuel_surcharge_effective_from":\s*)"[^"]*"', rf'\g<1>"{effective.isoformat()}"'),
        (r'("fuel_surcharge_source":\s*)"(?:[^"\\]|\\.)*"', lambda m: m.group(1) + json.dumps(source, ensure_ascii=False)),
        (r'(skirmshop\.labels/loaded-at:\s*)"[^"]*"', rf'\g<1>"{today.isoformat()}"'),
        (r'(skirmshop\.labels/fuel-source:\s*)"[^"]*"', lambda m: m.group(1) + json.dumps(source.rsplit(" - gmail:", 1)[0], ensure_ascii=False)),
    ]
    for pattern, repl in subs:
        text, n = re.subn(pattern, repl, text, count=1)
        if n != 1:
            raise ValueError(f"patrón sin casar al escribir: {pattern}")
    return text


def decide(text: str, gmail_id: str, value: float, effective: dt.date, source: str, today: dt.date) -> str | None:
    """Texto nuevo del fichero, o None si ya está aplicado o la evidencia es más vieja que lo vigente.

    Solo toca el documento YAML que lleva los campos fuel (cex-config.yaml trae más ConfigMaps)."""
    docs = text.split("\n---\n")
    i = next(i for i, d in enumerate(docs) if '"fuel_surcharge_pct"' in d)
    now = current(docs[i])
    if gmail_id in now["fuel_surcharge_source"]:
        return None
    if effective.isoformat() < now["fuel_surcharge_effective_from"]:
        return None
    docs[i] = apply(docs[i], value, effective, source, today)
    return "\n---\n".join(docs)


# ── Gmail ────────────────────────────────────────────────────────────────────

def gmail_token() -> str:
    cred = json.loads(os.environ["GMAIL_TOKEN_JSON"])
    data = urllib.parse.urlencode({"grant_type": "refresh_token", "client_id": cred["client_id"],
                                   "client_secret": cred["client_secret"],
                                   "refresh_token": cred["refresh_token"]}).encode()
    with urllib.request.urlopen(cred.get("token_uri", "https://oauth2.googleapis.com/token"), data, timeout=30) as r:
        return json.load(r)["access_token"]


def gmail_get(token: str, path: str) -> dict:
    req = urllib.request.Request(f"{GMAIL}/{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def latest(token: str, query: str) -> dict | None:
    ids = gmail_get(token, "messages?" + urllib.parse.urlencode({"q": query, "maxResults": 10})).get("messages", [])
    msgs = [gmail_get(token, f"messages/{m['id']}?format=full") for m in ids]
    return max(msgs, key=lambda m: int(m["internalDate"]), default=None)


def parts(payload: dict):
    yield payload
    for p in payload.get("parts", []) or []:
        yield from parts(p)


def b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def body_text(msg: dict) -> str:
    chunks = []
    for p in parts(msg["payload"]):
        if p.get("mimeType") in ("text/plain", "text/html") and p.get("body", {}).get("data"):
            chunks.append(b64(p["body"]["data"]).decode("utf-8", "replace"))
    return html.unescape(re.sub(r"<[^>]+>", " ", "\n".join(chunks)))


def msg_date(msg: dict) -> dt.date:
    return dt.datetime.fromtimestamp(int(msg["internalDate"]) / 1000, MADRID).date()


def subject(msg: dict) -> str:
    return next((h["value"] for h in msg["payload"]["headers"] if h["name"].lower() == "subject"), "")


def pdf_text(token: str, msg: dict) -> str:
    pdf = next((p for p in parts(msg["payload"]) if p.get("filename", "").lower().endswith(".pdf")), None)
    if not pdf:
        raise ValueError("Correos: la factura no trae PDF")
    data = gmail_get(token, f"messages/{msg['id']}/attachments/{pdf['body']['attachmentId']}")["data"]
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(b64(data))
        f.flush()
        return subprocess.run(["pdftotext", "-layout", f.name, "-"], check=True,
                              capture_output=True, text=True).stdout


def main() -> int:
    token = gmail_token()
    today = dt.datetime.now(MADRID).date()
    changes, errors = [], []

    try:
        msg = latest(token, CEX_QUERY)
        if msg:
            value, mes = parse_cex(body_text(msg))
            sent = msg_date(msg)
            source = (f"CEX email {sent} - tasa {mes} {sent.year if mes != 'diciembre' else sent.year - 1}"
                      f" - Real Decreto-ley 7/2026 + 9/2026 - gmail:{msg['id']}")
            new = decide(CEX_FILE.read_text(), msg["id"], value, sent, source, today)
            if new:
                CEX_FILE.write_text(new)
                changes.append(f"CEX {value:g} % desde {sent}")
    except Exception as exc:  # una fuente rota no tapa la otra
        errors.append(f"CEX: {exc}")

    try:
        msg = latest(token, CORREOS_QUERY)
        if msg:
            effective = correos_effective_from(body_text(msg))
            value = parse_correos(pdf_text(token, msg))
            source = f"Correos matriz - {subject(msg)} ({msg_date(msg)}): Supl. combustible {value:g} % - gmail:{msg['id']}"
            new = decide(CORREOS_FILE.read_text(), msg["id"], value, effective, source, today)
            if new:
                CORREOS_FILE.write_text(new)
                changes.append(f"Correos matriz {value:g} % desde {effective}")
    except Exception as exc:
        errors.append(f"Correos: {exc}")

    print("\n".join(changes) or "sin cambios")
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"summary={'; '.join(changes)}\n")
    for e in errors:
        print(f"ERROR {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
