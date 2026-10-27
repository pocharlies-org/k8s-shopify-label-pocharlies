"""Parser y reescritura de scripts/fuel_sync.py con extractos reales (email CEX 05-10-2026, factura Correos 30/09/26)."""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fuel_sync as fs  # noqa: E402

CEX_EMAIL = ("De acuerdo con los parámetros contractuales establecidos, el resultado del cálculo aplicable al mes de "
             "**septiembre** es:  \r\n---  \r\n**Tasa regulada: 3.094 %**  \r\n---  \r\nEste ajuste aparecerá")
CORREOS_PDF = ("Suplemento combustible 9,4 %      1   24,62   24,62   24,62   21,00%\n"
               "Suplementos\n                Supl. combustible 9,4 %    263,63      24,62   21,00 %\n")
CORREOS_BODY = "Te adjuntamos en formato electrónico la factura emitida con fecha   30/09/26 , firmada por la  Sociedad"
TODAY = dt.date(2026, 10, 27)


class Parse(unittest.TestCase):
    def test_cex(self):
        self.assertEqual(fs.parse_cex(CEX_EMAIL), (3.094, "septiembre"))

    def test_correos(self):
        self.assertEqual(fs.parse_correos(CORREOS_PDF), 9.4)

    def test_correos_whole_number(self):
        self.assertEqual(fs.parse_correos("Supl. combustible 10 %    168,90"), 10.0)

    def test_correos_effective_from_is_next_month(self):
        self.assertEqual(fs.correos_effective_from(CORREOS_BODY), dt.date(2026, 10, 1))
        self.assertEqual(fs.correos_effective_from("con fecha 31/12/26"), dt.date(2027, 1, 1))

    def test_missing_line_fails(self):
        for f, arg in ((fs.parse_cex, "sin tasa"), (fs.parse_correos, "sin suplemento"),
                       (fs.correos_effective_from, "sin fecha")):
            with self.assertRaises(ValueError):
                f(arg)

    def test_out_of_range_fails(self):
        with self.assertRaises(ValueError):
            fs.parse_cex("Tasa regulada: 45 %")


class Rewrite(unittest.TestCase):
    def setUp(self):
        self.cex = (ROOT / "k8s/cex-config.yaml").read_text()
        self.correos = (ROOT / "k8s/correos-surcharges.yaml").read_text()

    def test_cex_rewrites_only_fuel_doc(self):
        new = fs.decide(self.cex, "abc123", 4.5, dt.date(2026, 11, 5), "CEX email 2026-11-05 - x - gmail:abc123", TODAY)
        docs_old, docs_new = self.cex.split("\n---\n"), new.split("\n---\n")
        i = next(i for i, d in enumerate(docs_new) if "fuel_surcharge_pct" in d)
        self.assertEqual([d for j, d in enumerate(docs_old) if j != i], [d for j, d in enumerate(docs_new) if j != i])
        body = json.loads(docs_new[i].split("surcharges.json: |")[1].split("\n\n")[0])
        self.assertEqual(body["fuel_surcharge_pct"], 4.5)
        self.assertEqual(body["fuel_surcharge_effective_from"], "2026-11-05")
        self.assertEqual(body["dua_importacion_canarias_eur"], 16.0)  # el resto se conserva
        self.assertIn('loaded-at: "2026-10-27"', docs_new[i])
        self.assertIn('fuel-source: "CEX email 2026-11-05 - x"', docs_new[i])

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
