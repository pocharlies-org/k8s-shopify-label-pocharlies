#!/usr/bin/env python3
"""Generate CEX rate table JSON from the contractual PDF (vigencia 2026).

Source: SKIRMSHOP - TARIFAS CEX 2026.pdf (cliente 269650001, vigencia
01/01/2026 - 31/12/2026), attached by Gerardo Silva (CEX) to thread
19d66bbc944cc694 on 2026-04-07.

Output schema matches CexRateTableEntrySchema in
apps/correos-express-adapter/src/rating.ts.
"""
import json
import sys
from typing import Iterable

# Weight tiers used across most products. Last entry is the "Hasta 9.999"
# row with baseWeightKg + additionalKgCharge.
PAQ14_WEIGHTS = [1, 2, 3, 4, 5, 10, 15]
ECOMMERCE_WEIGHTS = [1, 2, 3, 4, 5, 10, 15]
FLAT_2KG_WEIGHTS = [2]  # ePaq24, Paq24, EntregaPlus — flat up to 2kg

# Zone IDs from CexRateZoneSchema. We do NOT emit 'baleares' rows when the
# product only offers "Islas Menores Baleares" — the routing maps 07xxx to
# 'baleares_minor' or 'mallorca' first.
ZONES_ECOMMERCE = [
    ("provincial", "Provincial"),
    ("regional", "Regional"),
    ("peninsula", "Pen."),
    ("peninsula_plus", "Pen.+"),
    ("baleares_minor", "Islas Menores Baleares"),
    ("baleares", "Baleares Interislas"),
    ("canarias_tnf_lpa", "Canarias - Tnf y Lpa"),
    ("canarias_minor", "Is. Menores Canarias"),
    ("canarias", "Canarias Interislas"),
    ("portugal", "Portugal Interislas"),
    ("azores", "Azores"),
    ("madeira", "Madeira"),
    ("mallorca", "MALLORCA"),
    ("special", "Especial"),
]

# PaqEcommerce, p.16. Primary ecommerce service — cheapest peninsular.
PAQECOMMERCE = {
    "service": "PaqEcommerce",
    "transit_days": 2,
    "zones": ZONES_ECOMMERCE,
    # rows[weight_idx] = list of charges aligned with zones above
    "rows": {
        1: [4.16, 4.16, 4.16, 4.16, 7.50, 6.42, 10.28, 11.30, 6.42, 10.03, 43.67, 42.44, 7.12, 25.49],
        2: [4.16, 4.16, 4.16, 4.16, 8.61, 6.42, 14.97, 16.47, 6.42, 10.03, 44.94, 42.91, 8.06, 25.49],
        3: [4.56, 4.56, 4.65, 4.65, 10.15, 7.44, 19.21, 21.14, 7.65, 11.95, 47.08, 44.18, 9.45, 29.35],
        4: [4.96, 4.96, 5.14, 5.14, 11.00, 8.46, 23.47, 25.84, 8.88, 13.87, 49.79, 46.35, 10.13, 33.21],
        5: [5.36, 5.36, 5.63, 5.63, 11.84, 9.48, 27.73, 30.50, 10.11, 15.79, 52.80, 48.77, 10.80, 37.07],
        10: [7.36, 7.36, 8.08, 8.08, 16.09, 14.58, 49.32, 54.25, 16.26, 25.39, 74.22, 68.04, 14.20, 56.37],
        15: [9.36, 9.36, 10.53, 10.53, 20.99, 19.68, 71.06, 78.18, 22.41, 34.99, 91.73, 83.45, 17.60, 75.67],
    },
    "additional": [0.40, 0.40, 0.49, 0.49, 0.81, 1.02, 4.11, 4.52, 1.23, 1.92, 6.05, 5.97, 0.64, 3.86],
}

# Paq punto, p.17. Pickup-point delivery — cheapest of all peninsular.
PAQPUNTO = {
    "service": "PaqPunto",
    "transit_days": 2,
    "zones": ZONES_ECOMMERCE,
    "rows": {
        1: [3.95, 3.95, 3.95, 3.95, 7.13, 6.10, 9.77, 10.74, 6.10, 9.53, 41.49, 40.32, 6.76, 24.22],
        2: [3.95, 3.95, 3.95, 3.95, 8.18, 6.10, 14.22, 15.65, 6.10, 9.53, 42.69, 40.76, 7.66, 24.22],
        3: [4.33, 4.33, 4.41, 4.41, 9.64, 7.08, 18.25, 20.08, 7.28, 11.35, 44.73, 41.97, 8.98, 27.88],
        4: [4.71, 4.71, 4.87, 4.87, 10.45, 8.05, 22.30, 24.55, 8.45, 13.18, 47.30, 44.03, 9.62, 31.55],
        5: [5.09, 5.09, 5.33, 5.33, 11.25, 9.03, 26.34, 28.98, 9.62, 15.00, 50.16, 46.33, 10.26, 35.22],
        10: [6.99, 6.99, 7.64, 7.64, 15.29, 13.88, 46.85, 51.54, 15.49, 24.12, 64.64, 64.64, 13.49, 53.56],
        15: [8.90, 8.90, 9.95, 9.95, 19.94, 18.74, 67.51, 74.27, 21.35, 33.24, 87.13, 79.28, 16.72, 71.91],
    },
    "additional": [0.38, 0.38, 0.47, 0.47, 0.77, 0.97, 3.90, 4.29, 1.17, 1.82, 5.75, 5.67, 0.61, 3.67],
}

# ePaq24, p.7. 24h service to peninsula + a few zones.
EPAQ24_ZONES = [
    ("provincial", "Provincial"),
    ("regional", "Regional"),
    ("peninsula", "Pen."),
    ("peninsula_plus", "Pen.+"),
    ("baleares", "Baleares Interislas"),
    ("canarias", "Canarias Interislas"),
    ("special", "Especial"),
]
EPAQ24 = {
    "service": "EPAQ24",
    "transit_days": 1,
    "zones": EPAQ24_ZONES,
    "rows": {
        2: [4.24, 4.24, 4.24, 4.24, 6.55, 6.55, 26.00],
    },
    "additional": [0.41, 0.41, 0.50, 0.50, 1.04, 1.25, 3.94],
}

# Paq24, p.8. 24h service with more zones (Portugal, Ceuta, Andorra, Gibraltar).
PAQ24_ZONES = [
    ("provincial", "Provincial"),
    ("regional", "Regional"),
    ("peninsula", "Pen."),
    ("peninsula_plus", "Pen.+"),
    ("baleares", "Baleares Interislas"),
    ("canarias", "Canarias Interislas"),
    ("portugal", "Portugal Interislas"),
    ("ceuta_melilla", "Ceuta y Melilla"),
    ("andorra", "Andorra"),
    ("gibraltar", "Gibraltar"),
    ("special", "Especial"),
]
PAQ24 = {
    "service": "PAQ24",
    "transit_days": 1,
    "zones": PAQ24_ZONES,
    "rows": {
        2: [4.24, 4.24, 4.24, 4.24, 6.55, 6.55, 10.23, 18.55, 16.95, 45.39, 26.00],
    },
    "additional": [0.41, 0.41, 0.50, 0.50, 1.04, 1.25, 1.96, 2.26, 1.95, 1.15, 3.94],
}

# Paq14, p.5. AM-14h delivery. Already in prod but rewrite for completeness.
PAQ14_ZONES = [
    ("provincial", "Provincial"),
    ("regional", "Regional"),
    ("peninsula", "Pen."),
    ("peninsula_plus", "Pen.+"),
    ("baleares_minor", "Islas Menores Baleares"),
    ("canarias_tnf_lpa", "Canarias - Tnf y Lpa"),
    ("mallorca", "MALLORCA"),
]
PAQ14 = {
    "service": "PAQ14",
    "transit_days": 1,
    "zones": PAQ14_ZONES,
    "rows": {
        1: [5.94, 6.81, 6.99, 7.43, 10.61, 10.60, 10.07],
        2: [6.27, 7.15, 7.36, 7.78, 12.16, 15.42, 11.39],
        3: [6.60, 7.48, 7.69, 8.14, 14.35, 19.82, 13.35],
        4: [6.94, 7.82, 8.06, 8.52, 15.57, 24.20, 14.31],
        5: [7.16, 8.05, 8.29, 8.80, 16.76, 28.57, 15.28],
        10: [9.36, 10.31, 10.61, 10.93, 22.76, 50.85, 20.09],
        15: [11.39, 12.58, 12.90, 13.23, 29.70, 73.26, 24.90],
    },
    "additional": [0.41, 0.45, 0.52, 0.65, 4.81, 3.39, 3.25],
}

# Islas Express, p.9. CEX product "Canarias Express" = código 67 (see
# CEX_SERVICE_TO_PRODUCT in correos-express-adapter/src/services.ts). routing
# decides service '67' for Canarias, the label path (grabacion) sends producto
# '67', and the checkout title maps '67'→"Correos Express Canarias". The table
# MUST publish these island rows under '67' (was '26', which never matched the
# routed '67' in cexServiceMatches → Canarias fell through to the bogus EPAQ24
# interislas 6.55€). The Baleares-minor/Mallorca rows here are unused (routing
# uses the BALEARES product for Baleares) but kept for contract completeness.
ISLAS_EXPRESS_ZONES = [
    ("baleares_minor", "Islas Menores Baleares"),
    ("canarias_tnf_lpa", "Canarias - Tnf y Lpa"),
    ("canarias_minor", "Is. Menores Canarias"),
    ("azores", "Azores"),
    ("madeira", "Madeira"),
    ("mallorca", "MALLORCA"),
]
ISLAS_EXPRESS = {
    "service": "67",
    "transit_days": 5,
    "zones": ISLAS_EXPRESS_ZONES,
    "rows": {
        1: [7.65, 10.49, 11.53, 44.54, 43.29, 7.26],
        2: [8.78, 15.27, 16.80, 45.84, 43.77, 8.22],
        3: [10.35, 19.59, 21.56, 48.02, 45.06, 9.64],
        4: [11.22, 23.94, 26.36, 50.79, 47.28, 10.33],
        5: [12.08, 28.28, 31.11, 53.86, 49.75, 11.02],
        10: [16.41, 50.31, 55.34, 75.70, 69.40, 14.48],
        15: [21.41, 72.48, 79.74, 93.55, 85.12, 17.95],
    },
    "additional": [0.83, 4.19, 4.61, 6.17, 6.09, 0.65],
}


def emit_entries(product: dict) -> Iterable[dict]:
    for zone_idx, (zone_id, _label) in enumerate(product["zones"]):
        for weight, prices in sorted(product["rows"].items()):
            yield {
                "contractScope": "direct",
                "service": product["service"],
                "zone": zone_id,
                "maxWeightKg": weight,
                "totalCharges": f"{prices[zone_idx]:.2f}",
                "currency": "EUR",
                "transitDays": product["transit_days"],
            }
        # Open-ended last row: "Hasta 9.999 (CadaKG Adicional)".
        max_weight = max(product["rows"].keys())
        yield {
            "contractScope": "direct",
            "service": product["service"],
            "zone": zone_id,
            "maxWeightKg": 9999,
            "totalCharges": f"{product['rows'][max_weight][zone_idx]:.2f}",
            "baseWeightKg": max_weight,
            "additionalKgCharge": f"{product['additional'][zone_idx]:.2f}",
            "currency": "EUR",
            "transitDays": product["transit_days"],
        }


def main() -> None:
    products = [PAQECOMMERCE, EPAQ24, PAQ24, PAQ14, PAQPUNTO, ISLAS_EXPRESS]
    entries = [e for p in products for e in emit_entries(p)]
    json.dump(entries, sys.stdout, ensure_ascii=False, indent=2)
    sys.stderr.write(f"emitted {len(entries)} rate rows across {len(products)} products\n")


if __name__ == "__main__":
    main()
