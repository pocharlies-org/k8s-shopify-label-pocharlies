#!/usr/bin/env python3
"""Recargo de combustible de CEX y Correos matriz → ConfigMaps de GitOps (SKIRM-312, SKIRM-398).

Determinista, sin LLM. Lo corre `.github/workflows/fuel-sync.yml`; el workflow commitea si cambia algo.

- CEX: tabla contractual de tramos (contrato 54097436) sobre el precio medio nacional del gasóleo de
  automoción con impuestos del informe mensual de MITECO: el gasóleo del mes M-2 fija el % del mes M
  (agosto 184,29 c€/l → octubre 11,8 %). Vigente desde el día 1 del mes M. El email «Tasa regulada» de
  no-reply@correosexpress.com NO aplica a Skirmshop (es para clientes con bonificación; CEX, 05-11-2026).
- Correos matriz: no manda email del recargo; sale en la factura mensual de facturas.clientes@correos.com
  (PDF, línea «Supl. combustible 9,4 %»). Vigente desde el día 1 del mes siguiente a la fecha de factura.

Sin dato nuevo no escribe nada; un patrón que no casa o un % fuera de rango sale con error (el watchdog
labels-fuel-freshness avisa si el mes queda sin aplicar). Nunca retrocede la fecha vigente.

Credencial (solo Correos): GMAIL_TOKEN_JSON = authorized_user de Google (client_id, client_secret,
refresh_token) con solo gmail.readonly sobre info@e-dani.com (info@skirmshop.es es su alias).
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
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
REPO = Path(__file__).resolve().parent.parent
CEX_FILE = REPO / "k8s/cex-config.yaml"
CORREOS_FILE = REPO / "k8s/correos-surcharges.yaml"
MITECO = "https://www.miteco.gob.es"
MITECO_INDEX = f"{MITECO}/es/energia/hidrocarburos-nuevos-combustibles/petroleo/informes/informes-mensuales.html"
CORREOS_QUERY = "from:facturas.clientes@correos.com has:attachment newer_than:45d"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
# Tabla contractual de CEX (Gerardo Silva, comercial CEX, 05-11-2026): (precio del gasóleo hasta, en c€/l
# redondeado al céntimo de €, → % de suplemento). El email trae «1,78–1,82 € → 0,11 €», errata: 11,2 % por
# el paso de 0,6 puntos de los tramos vecinos.
CEX_TRAMOS = [(92, 0.0), (97, 2.0), (102, 2.5), (107, 3.0), (112, 3.25), (117, 3.75), (122, 4.25),
              (127, 5.0), (132, 5.5), (137, 6.0), (142, 6.5), (147, 7.0), (152, 7.6), (157, 8.2),
              (162, 8.8), (167, 9.4), (172, 10.0), (177, 10.6), (182, 11.2), (187, 11.8), (192, 12.4),
              (197, 13.0), (202, 13.6), (207, 14.2), (212, 14.8), (217, 15.4), (222, 16.0), (227, 16.6),
              (232, 17.2), (237, 17.8), (242, 18.4), (247, 19.0), (252, 19.6), (257, 20.2), (262, 20.8)]


def pct(raw: str) -> float:
    value = float(raw.replace(",", "."))
    if not 0 < value < 30:
        raise ValueError(f"recargo fuera de rango: {raw} %")
    return value


def cex_target(today: dt.date) -> tuple[dt.date, str, int]:
    """Mes M de hoy → (día 1 de M, mes y año del informe MITECO de M-2 que fija su %)."""
    i = today.month - 3  # índice 0-based de M-2; negativo = año anterior
    return today.replace(day=1), MESES[i % 12], today.year + i // 12


def miteco_report_url(index_html: str, mes: str, year: int) -> str:
    """Página de informes mensuales → URL del PDF de ese mes. El nombre varía («Julio%202026_p.pdf»,
    «Marzo%202026__.pdf», «Julio_2025_v1.pdf»); la página lista primero la versión vigente."""
    for href in re.findall(r'href="([^"]*/InformesMensuales/DatosBibliotecaConsumer/\d{4}/[^"]+\.pdf)"', index_html):
        name = urllib.parse.unquote(href.rsplit("/", 1)[1]).lower()
        if re.match(rf"{mes}[ _]+{year}(?!\d)", name):
            return urllib.parse.urljoin(MITECO, href)
    raise ValueError(f"MITECO: no hay informe de {mes} {year} en {MITECO_INDEX}")


def parse_miteco(pdf_text: str, mes: str, year: int) -> str:
    """Texto (pdftotext -layout) del informe → PVP medio nacional del gasóleo de automoción, «187,44» (c€/l).
    El bloque «(SIN IMPUESTOS)» va antes con la misma forma: se ancla en «(CON IMPUESTOS)»."""
    if f"DATOS DE {mes.upper()} DE {year}" not in pdf_text:
        raise ValueError(f"MITECO: el PDF no es el informe de {mes} {year}")
    m = re.search(r"\(CON IMPUESTOS\).*?A\. PRECIOS MEDIOS NACIONALES.*?^\s*Gas[oó]leo automoci[oó]n\s+(\d{2,3},\d{2})\s*$",
                  pdf_text, re.S | re.M)
    if not m:
        raise ValueError("MITECO: el informe no trae el PVP medio nacional del gasóleo de automoción")
    return m.group(1)


def cex_tramo(gasoleo: str) -> float:
    """«187,44» c€/l → % de la tabla contractual (1,87 €/l → 11,8 %).
    shortcut: redondeo al céntimo de €/l (la tabla salta de 1,87 a 1,88); si CEX dice truncar, ROUND_DOWN."""
    cents = int(Decimal(gasoleo.replace(",", ".")).quantize(Decimal(1), ROUND_HALF_UP))
    for hasta, value in CEX_TRAMOS:
        if cents <= hasta:
            return value
    raise ValueError(f"CEX: gasóleo {gasoleo} c€/l por encima de la tabla contractual")


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
        (r'(skirmshop\.labels/fuel-source:\s*)"[^"]*"', lambda m: m.group(1) + json.dumps(re.sub(r" - (?:gmail|miteco):\S+$", "", source), ensure_ascii=False)),
    ]
    for pattern, repl in subs:
        text, n = re.subn(pattern, repl, text, count=1)
        if n != 1:
            raise ValueError(f"patrón sin casar al escribir: {pattern}")
    return text


def decide(text: str, ref: str, value: float, effective: dt.date, source: str, today: dt.date) -> str | None:
    """Texto nuevo del fichero, o None si ya está aplicado o la evidencia es más vieja que lo vigente.

    Solo toca el documento YAML que lleva los campos fuel (cex-config.yaml trae más ConfigMaps)."""
    docs = text.split("\n---\n")
    i = next(i for i, d in enumerate(docs) if '"fuel_surcharge_pct"' in d)
    now = current(docs[i])
    if ref in now["fuel_surcharge_source"]:
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


def pdf_to_text(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        return subprocess.run(["pdftotext", "-layout", f.name, "-"], check=True,
                              capture_output=True, text=True).stdout


def pdf_text(token: str, msg: dict) -> str:
    pdf = next((p for p in parts(msg["payload"]) if p.get("filename", "").lower().endswith(".pdf")), None)
    if not pdf:
        raise ValueError("Correos: la factura no trae PDF")
    return pdf_to_text(b64(gmail_get(token, f"messages/{msg['id']}/attachments/{pdf['body']['attachmentId']}")["data"]))


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "skirmshop-labels fuel-sync"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main() -> int:
    today = dt.datetime.now(MADRID).date()
    changes, errors = [], []

    try:
        effective, mes, year = cex_target(today)
        url = miteco_report_url(fetch(MITECO_INDEX).decode("utf-8", "replace"), mes, year)
        gasoleo = parse_miteco(pdf_to_text(fetch(url)), mes, year)
        value = cex_tramo(gasoleo)
        ref = f"miteco:{year}-{MESES.index(mes) + 1:02d}"
        source = f"CEX tramo contractual - MITECO {mes} {year}: gasóleo automoción {gasoleo} c€/l → {value:g} % - {ref}"
        new = decide(CEX_FILE.read_text(), ref, value, effective, source, today)
        if new:
            CEX_FILE.write_text(new)
            changes.append(f"CEX {value:g} % desde {effective}")
    except Exception as exc:  # una fuente rota no tapa la otra
        errors.append(f"CEX: {exc}")

    try:
        token = gmail_token()
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
