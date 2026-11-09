"""The CEX rate table ConfigMap is the generator's output, one quote per product (SKIRM-424)."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def configmap_rows() -> list:
    doc = (ROOT / "k8s/cex-config.yaml").read_text().split("\n---\n")[0]
    block = doc.split("  rate-table.json: |\n", 1)[1]
    return json.loads("\n".join(line[4:] for line in block.splitlines()))


def generator_rows() -> list:
    out = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/generate-cex-rate-table.py")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


class CexRateTableTest(unittest.TestCase):
    def test_configmap_and_json_match_generator(self):
        rows = generator_rows()
        self.assertEqual(configmap_rows(), rows)
        self.assertEqual(json.loads((ROOT / "scripts/cex-rate-table.json").read_text()), rows)

    def test_no_paq_ecommerce_rows(self):
        # Labels ships "Paq Ecommerce" as product 93 = ePaq 24; its rows only duplicated ePaq24.
        services = {row["service"] for row in configmap_rows()}
        self.assertNotIn("PaqEcommerce", services)

    def test_epaq24_peninsula_is_contract_rate(self):
        rows = [
            r for r in configmap_rows()
            if r["service"] == "EPAQ24" and r["zone"] == "peninsula" and r["maxWeightKg"] == 2
        ]
        self.assertEqual([r["totalCharges"] for r in rows], ["4.24"])


if __name__ == "__main__":
    unittest.main()
