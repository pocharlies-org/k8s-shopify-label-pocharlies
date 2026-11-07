"""Parser y reescritura de scripts/fuel_sync.py con extractos reales (informe MITECO septiembre 2026, factura Correos 30/09/26)."""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fuel_sync as fs  # noqa: E402

# pdftotext -layout del informe MITECO de septiembre 2026: el bloque SIN IMPUESTOS va antes con la misma forma.
MITECO_PDF = """PRECIOS DE CARBURANTES Y COMBUSTIBLES
DATOS DE SEPTIEMBRE DE 2026
                                            RESULTADOS DEL MUESTREO DE EE.SS.
                                                    (SIN IMPUESTOS)

                                                                     septiembre 26
  A. PRECIOS MEDIOS NACIONALES
                                                                           PSI (c€/l)

  Gasolina eurosúper s/Pb I.O.95                                             114,04

  Gasóleo automoción                                                         137,01

  Gasóleo automoción                                  134,04                 145,37                  11,32
                                            RESULTADOS DEL MUESTREO DE EE.SS.
                                                    (CON IMPUESTOS)

                                                                    septiembre 26
A. PRECIOS MEDIOS NACIONALES
                                                                         PVP (c€/l)

Gasolina eurosúper s/Pb I.O.95                                             189,13

Gasóleo automoción                                                         187,44

Gasóleo automoción                                  183,85                 197,55                  13,70
"""
MITECO_INDEX = (
    '<a href="/content/dam/miteco/es/energia/files-1/petroleo/Informes/InformesMensuales/DatosBibliotecaConsumer/2026/Septiembre%202026.pdf">'
    '<a href="/content/dam/miteco/es/energia/files-1/petroleo/Informes/InformesMensuales/DatosBibliotecaConsumer/2026/Julio%202026_p.pdf">'
    '<a href="/content/dam/miteco/es/energia/files-1/petroleo/Informes/InformesMensuales/DatosBibliotecaConsumer/2025/Julio_2025_v1.pdf">')
CORREOS_PDF = ("Suplemento combustible 9,4 %      1   24,62   24,62   24,62   21,00%\n"
               "Suplementos\n                Supl. combustible 9,4 %    263,63      24,62   21,00 %\n")
CORREOS_BODY = "Te adjuntamos en formato electrónico la factura emitida con fecha   30/09/26 , firmada por la  Sociedad"
TODAY = dt.date(2026, 10, 27)


class Parse(unittest.TestCase):
    def test_miteco_gasoleo_con_impuestos(self):
        self.assertEqual(fs.parse_miteco(MITECO_PDF, "septiembre", 2026), "187,44")

    def test_miteco_wrong_month_fails(self):
        with self.assertRaises(ValueError):
            fs.parse_miteco(MITECO_PDF, "octubre", 2026)

    def test_miteco_report_url(self):
        base = "https://www.miteco.gob.es/content/dam/miteco/es/energia/files-1/petroleo/Informes/InformesMensuales/DatosBibliotecaConsumer"
        self.assertEqual(fs.miteco_report_url(MITECO_INDEX, "julio", 2026), f"{base}/2026/Julio%202026_p.pdf")
        self.assertEqual(fs.miteco_report_url(MITECO_INDEX, "julio", 2025), f"{base}/2025/Julio_2025_v1.pdf")
        with self.assertRaises(ValueError):
            fs.miteco_report_url(MITECO_INDEX, "octubre", 2026)

    def test_cex_target_is_two_months_back(self):
        self.assertEqual(fs.cex_target(dt.date(2026, 11, 6)), (dt.date(2026, 11, 1), "septiembre", 2026))
        self.assertEqual(fs.cex_target(dt.date(2027, 1, 3)), (dt.date(2027, 1, 1), "noviembre", 2026))
        self.assertEqual(fs.cex_target(dt.date(2027, 2, 9)), (dt.date(2027, 2, 1), "diciembre", 2026))

    def test_cex_tramo(self):
        # Gerardo Silva (CEX) 05-11-2026: agosto 184,29 y septiembre 187,44 → 11,80 % en octubre y noviembre.
        self.assertEqual(fs.cex_tramo("184,29"), 11.8)
        self.assertEqual(fs.cex_tramo("187,44"), 11.8)
        self.assertEqual(fs.cex_tramo("187,50"), 12.4)  # 1,875 €/l redondea a 1,88
        self.assertEqual(fs.cex_tramo("140,23"), 6.5)
        self.assertEqual(fs.cex_tramo("91,00"), 0.0)
        with self.assertRaises(ValueError):
            fs.cex_tramo("263,00")

    def test_correos(self):
        self.assertEqual(fs.parse_correos(CORREOS_PDF), 9.4)

    def test_correos_whole_number(self):
        self.assertEqual(fs.parse_correos("Supl. combustible 10 %    168,90"), 10.0)

    def test_correos_effective_from_is_next_month(self):
        self.assertEqual(fs.correos_effective_from(CORREOS_BODY), dt.date(2026, 10, 1))
        self.assertEqual(fs.correos_effective_from("con fecha 31/12/26"), dt.date(2027, 1, 1))

    def test_missing_line_fails(self):
        for f, arg in ((fs.parse_correos, "sin suplemento"), (fs.correos_effective_from, "sin fecha")):
            with self.assertRaises(ValueError):
                f(arg)

    def test_out_of_range_fails(self):
        with self.assertRaises(ValueError):
            fs.parse_correos("Supl. combustible 45 %")


class Rewrite(unittest.TestCase):
    def setUp(self):
        self.cex = (ROOT / "k8s/cex-config.yaml").read_text()
        self.correos = (ROOT / "k8s/correos-surcharges.yaml").read_text()

    def test_cex_rewrites_only_fuel_doc(self):
        new = fs.decide(self.cex, "miteco:2026-10", 12.4, dt.date(2026, 12, 1), "CEX tramo contractual - x - miteco:2026-10", TODAY)
        docs_old, docs_new = self.cex.split("\n---\n"), new.split("\n---\n")
        i = next(i for i, d in enumerate(docs_new) if "fuel_surcharge_pct" in d)
        self.assertEqual([d for j, d in enumerate(docs_old) if j != i], [d for j, d in enumerate(docs_new) if j != i])
        body = json.loads(docs_new[i].split("surcharges.json: |")[1].split("\n\n")[0])
        self.assertEqual(body["fuel_surcharge_pct"], 12.4)
        self.assertEqual(body["fuel_surcharge_effective_from"], "2026-12-01")
        self.assertEqual(body["dua_importacion_canarias_eur"], 16.0)  # el resto se conserva
        self.assertIn('loaded-at: "2026-10-27"', docs_new[i])
        self.assertIn('fuel-source: "CEX tramo contractual - x"', docs_new[i])

    def test_same_miteco_month_is_noop(self):
        self.assertIsNone(fs.decide(self.cex, "miteco:2026-09", 11.8, dt.date(2026, 11, 1), "x", TODAY))

    def test_same_gmail_id_is_noop(self):
        new = fs.decide(self.correos, "abc123", 9.4, dt.date(2026, 11, 1), "Correos matriz - f - gmail:abc123", TODAY)
        self.assertIsNone(fs.decide(new, "abc123", 9.4, dt.date(2026, 11, 1), "otra", TODAY))

    def test_older_evidence_is_noop(self):
        self.assertIsNone(fs.decide(self.correos, "old", 8.2, dt.date(2026, 9, 1), "Correos matriz - vieja", TODAY))

    def test_source_with_quotes_stays_valid_json(self):
        new = fs.decide(self.correos, "q1", 9.4, dt.date(2026, 11, 1), 'Correos matriz - «Factura "N.1"» - gmail:q1', TODAY)
        self.assertEqual(fs.current(new)["fuel_surcharge_source"], 'Correos matriz - «Factura "N.1"» - gmail:q1')


if __name__ == "__main__":
    unittest.main()
