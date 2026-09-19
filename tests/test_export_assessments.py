import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts import export_assessments as exporter


class ExportAssessmentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.latest, cls.model, cls.report = exporter.verified_sources()

    def test_preserves_every_company_probability_feature_and_abstention(self):
        companies = exporter.assessments(self.latest, self.model, self.report)
        self.assertEqual(len(companies), 1286)
        self.assertEqual(sum(c["predictive"]["probability_estimate"] is None for c in companies), 276)
        for row, company in zip(self.latest["predictions"], companies):
            p = company["predictive"]
            self.assertEqual(company["id"], row["company_id"])
            self.assertEqual(p["probability_estimate"], row["p_proxy"])
            self.assertEqual(p["observed_features"], row["features"])
            self.assertEqual(p["coverage"], row["coverage"])
            self.assertEqual(p["missing_reason"], row["missing_reason"])
            self.assertEqual(p["unavailable_reasons"], row["unavailable_reasons"])
            self.assertEqual((p["horizon_start"], p["horizon_end"]), ("2026-09-01", "2026-11-30"))
            for key in ("opening_cash_cents", "buffer_cents", "health"):
                self.assertIsNone(company[key])
            for key in ("history", "flows", "drivers"):
                self.assertEqual(company[key], [])
            self.assertNotIn("known_on", json.dumps(company))

    def test_export_is_reproducible_and_refuses_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            manifest = exporter.export(output)
            self.assertEqual(exporter.export(output, check=True), manifest)
            self.assertEqual(exporter.export(output), manifest)
            (output / "companies.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "Immutable output differs"):
                exporter.export(output)
            self.assertEqual((output / "companies.json").read_text(), "[]")

    def test_seal_and_integrity_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            payload = (exporter.SOURCE / "latest_predictions.json").read_bytes()
            (output / "latest_predictions.json").write_bytes(payload + b" ")
            with self.assertRaisesRegex(ValueError, "Sealed hash mismatch"):
                exporter.verified_sources(output)
            (output / "latest_predictions.json").write_bytes(payload)
            (output / "latest_predictions.json.integrity.json").write_text('{"sha256":"wrong"}')
            with self.assertRaisesRegex(ValueError, "Integrity mismatch"):
                exporter.verified_sources(output)

    def test_invalid_probabilities_scope_and_abstentions_rejected(self):
        for update in ({"p_proxy": -0.1}, {"p_proxy": 1.1}, {"p_proxy": float("nan")}, {"p_proxy": True}, {"p_proxy": None}, {"accepted": False}, {"eligible": False}, {"horizon_months": 90}, {"as_of": "2026-09-01"}, {"target": "health"}):
            with self.subTest(update=update):
                latest = copy.deepcopy(self.latest)
                latest["predictions"][0].update(update)
                with self.assertRaises(ValueError):
                    exporter.assessments(latest, self.model, self.report)
        latest = copy.deepcopy(self.latest)
        latest["predictions"][1]["unavailable_reasons"] = []
        with self.assertRaises(ValueError):
            exporter.assessments(latest, self.model, self.report)

    def test_calendar_months_are_not_ninety_days(self):
        self.assertEqual(exporter.calendar_horizon("2023-11-30"), ("2023-12-01", "2024-02-29"))
        with self.assertRaises(ValueError):
            exporter.calendar_horizon("2026-08-30")


if __name__ == "__main__":
    unittest.main()
