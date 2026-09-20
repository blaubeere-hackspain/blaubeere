import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from scripts.build_predictive_bundle import build
from scripts.financial_v3_contract import canonical_bytes
from scripts.financial_v3_engine import FinancialInputs, evaluate_company_month
from scripts.financial_v3_score import explain_delta
from services.predictive.predictive_model.bundle import Bundle
from services.predictive.predictive_model.contract import (
    InputError,
    context,
    decode,
    normalize,
)
from services.predictive.predictive_model.features import (
    FEATURE_NAMES,
    effective_records,
    prepare,
)
from services.predictive.predictive_model.runtime import assess
from services.predictive.predictive_model.schema import input_schema, output_schema
from tests.test_financial_v3_engine import fixture

ROOT = Path(__file__).resolve().parents[1]
OBSERVED = "2027-01-01T00:00:00Z"


def sample_snapshot():
    inputs = fixture()
    data = json.loads(json.dumps(asdict(inputs)).replace("2025-", "2026-"))
    for account in data["records"]["account"]:
        account["product_type"] = "checking"
    classifications = []
    for row in data["records"]["cash_movement"]:
        source = {
            **row["source_ref"],
            "record_id": row["movement_id"] + "-classification",
        }
        row["classification_evidence"] = source["record_id"]
        classifications.append(
            {
                "company_id": row["company_id"],
                "movement_id": row["movement_id"],
                "rule_id": "fixture:operating",
                "source_ref": source,
            }
        )
    invoices = []
    for row in data["records"]["obligation"]:
        invoices.append(
            {
                "company_id": row["company_id"],
                "invoice_id": row["invoice_id"],
                "side": "ap",
                "issued_on": row["source_ref"]["known_on"][:10],
                "due_on": row["due_date"],
                "amount_native": row["principal_or_face_amount"],
                "currency": row["currency"],
                "fx_at": None,
                "source_ref": {
                    **row["source_ref"],
                    "record_id": row["invoice_id"] + "-document",
                },
            }
        )
    return {
        "schema_version": "predictive-assessment-1",
        "request_id": "example-request",
        "company_id": "invented-company",
        "group_id": "invented-group",
        "snapshot_id": "example-snapshot",
        "as_of": "2026-06-30",
        "view": "as_of",
        "reporting_currency": "EUR",
        **data,
        "classifications": classifications,
        "invoices": invoices,
    }


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.release = Path(cls.temp.name) / "release"
        cls.meta = build(cls.release)
        cls.bundle = Bundle.load(
            cls.release / "bundle", expected_sha256=cls.meta["bundle_sha256"]
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_model(self, data=None):
        return assess(
            sample_snapshot() if data is None else data,
            self.bundle,
            observed_at=OBSERVED,
        )

    def test_financial_and_change_parity(self):
        data = normalize(sample_snapshot(), observed_at=OBSERVED)
        from scripts.financial_v3_cash import CashTerms
        from scripts.financial_v3_obligations import (
            ObligationCoverage,
            PrincipalObservation,
        )

        def reference(month):
            ctx = context(data, month)
            inputs = FinancialInputs(
                data["records"],
                [
                    CashTerms(
                        **{
                            **r,
                            "invalid_stock_values": tuple(r["invalid_stock_values"]),
                        }
                    )
                    for r in data["cash_terms"]
                ],
                [ObligationCoverage(**r) for r in data["obligation_coverage"]],
                [PrincipalObservation(**r) for r in data["principal_observations"]],
            )
            return evaluate_company_month(inputs, ctx, observed_at=OBSERVED)

        actual = self.run_model(data)
        current, previous = reference("2026-06-01"), reference("2026-05-01")
        self.assertIsNotNone(current["score"]["value"])
        self.assertEqual(actual["financial"], current)
        self.assertEqual(actual["change"], explain_delta(previous, current))
        self.assertIsNone(
            actual["predictions"]["current_receipt_dip_3m"]["probabilities"]
        )
        self.assertIsNotNone(
            actual["predictions"]["receipt_contraction_3m"]["probabilities"]
        )

    def test_18_feature_parity_with_historical_helper(self):
        from scripts.financial_v3_dataset_model import monthly_features

        data = normalize(sample_snapshot(), observed_at=OBSERVED)
        ctx = context(data, "2026-06-01")
        actual = prepare(data, ctx, effective_records(data, ctx))
        flows = {
            f"2026-{m:02d}-01": {
                "tiene_actividad_caja": True,
                "n_sin_eur": 0,
                "cobros_operativos_conocido_eur": 1000.0,
                "pagos_operativos_conocido_eur": 800.0,
                "operativo_min_eur": 200.0,
                "operativo_max_eur": 200.0,
                "cobertura_clasificacion_pct": 1.0,
                "cobertura_eur_pct": 1.0,
            }
            for m in (4, 5, 6)
        }
        docs = {
            "2026-06-01": {
                "ap_due_face": 800.0,
                "ap_issued_known": 800.0,
                "ar_issued_known": 0.0,
                "unknown_due_rows": 0,
                "due_missing_eur_rows": 0,
            }
        }
        expected = monthly_features(
            "invented-company",
            "invented-group",
            "train",
            ctx["month"],
            flows,
            docs,
            {"2026-06-01": {"servicio_deuda_conocido_eur": 0.0}},
        )
        self.assertEqual(actual["values"], expected["values"])
        self.assertEqual(actual["eligible"], expected["eligible"])
        self.assertEqual(actual["reasons"], expected["reasons"])
        self.assertEqual(tuple(actual["values"]), FEATURE_NAMES)

    def test_repeated_permuted_and_duplicate_inputs_are_deterministic(self):
        data = sample_snapshot()
        before = self.run_model(data)
        data["request_id"] = "another-request"
        for rows in data["records"].values():
            rows.reverse()
            if rows:
                rows.append(copy.deepcopy(rows[0]))
        for name in (
            "cash_terms",
            "invoices",
            "classifications",
            "obligation_coverage",
        ):
            data[name].reverse()
            data[name].append(copy.deepcopy(data[name][0]))
        original_input = copy.deepcopy(data)
        after = self.run_model(data)
        before.pop("request_id")
        after.pop("request_id")
        self.assertEqual(before, after)
        self.assertEqual(data, original_input)

    def test_new_company_new_month_correction_no_server_state(self):
        data = sample_snapshot()
        original = self.run_model(data)
        data["as_of"] = "2026-07-31"
        data["company_id"] = "new-company"
        for rows in [
            *data["records"].values(),
            *(
                data[k]
                for k in (
                    "cash_terms",
                    "invoices",
                    "classifications",
                    "obligation_coverage",
                    "principal_observations",
                )
            ),
        ]:
            for row in rows:
                row["company_id"] = "new-company"
        newer = self.run_model(data)
        self.assertEqual(newer["company_id"], "new-company")
        self.assertIsNotNone(
            newer["predictions"]["receipt_contraction_3m"]["probabilities"]
        )
        data["snapshot_id"] = "corrected"
        data["records"]["cash_movement"][-2]["amount_native"] = "5000"
        corrected = self.run_model(data)
        self.assertNotEqual(corrected["input_sha256"], newer["input_sha256"])
        self.assertEqual(self.run_model(), original)

    def test_future_facts_do_not_enter_asof(self):
        data = sample_snapshot()
        before = self.run_model(data)
        row = copy.deepcopy(data["records"]["cash_movement"][0])
        row.update(
            movement_id="future-observation",
            amount_native="99999",
            booked_at="2026-06-15T12:00:00Z",
            classification_evidence=None,
            economic_bucket="unknown",
        )
        row["source_ref"].update(
            record_id="future-observation", known_on="2026-07-01T00:00:00Z"
        )
        data["records"]["cash_movement"].append(row)
        after = self.run_model(data)
        for key in ("financial", "change", "predictions", "model_inputs"):
            self.assertEqual(after[key], before[key])
        data["view"] = "reconstructed_retrospective"
        self.assertIn(
            "retrospective_not_historically_known", self.run_model(data)["warnings"]
        )

    def test_future_classification_is_not_backfilled(self):
        data = sample_snapshot()
        row = next(
            r for r in data["classifications"] if r["movement_id"] == "receipt-6"
        )
        row["source_ref"]["known_on"] = "2026-07-01T00:00:00Z"
        result = self.run_model(data)
        self.assertEqual(result["model_inputs"]["values"]["receipts_known"], 0)
        self.assertEqual(
            result["model_inputs"]["values"]["classification_coverage"], 0.5
        )
        self.assertIsNone(result["financial"]["score"]["value"])

    def test_foreign_payments_are_not_relabeled_as_reporting_currency(self):
        data = sample_snapshot()
        for rows in [*data['records'].values(), data['invoices'], data['cash_terms']]:
            for row in rows:
                if 'currency' in row:
                    row['currency'] = 'USD'
        result = self.run_model(data)
        for evidence in result['financial']['obligation_history']:
            self.assertEqual(evidence['currency'], 'EUR')
            self.assertIsNone(evidence['allocated_paid'])
            self.assertIsNone(evidence['paid_by_kind']['operating_ap'])
            self.assertTrue(all(row['currency'] == 'USD' for row in evidence['items']))
        self.assertIsNone(result['financial']['obligations']['allocated_paid'])
        self.assertIsNone(result['financial']['score']['value'])

    def test_pending_and_noncash_unverified_rows_do_not_change_predictor(self):
        expected = self.run_model()
        for noncash in (False, True):
            data = sample_snapshot()
            row = copy.deepcopy(data['records']['cash_movement'][0])
            row.update(movement_id='outside-perimeter', booked_at='2026-06-15T12:00:00Z',
                booking_status='booked' if noncash else 'pending', economic_bucket='unknown',
                classification_evidence=None, obligation_allocation_ids=[])
            row['source_ref'].update(record_id='outside-perimeter', known_on=None, availability_basis='unknown')
            if noncash:
                account = copy.deepcopy(data['records']['account'][0])
                account.update(account_id='outside-cash', product_type='tpv')
                account['source_ref']['record_id'] = 'outside-account'
                data['records']['account'].append(account)
                row['account_id'] = 'outside-cash'
            data['records']['cash_movement'].append(row)
            result = self.run_model(data)
            self.assertEqual(result['model_inputs'], expected['model_inputs'])
            self.assertEqual(result['predictions'], expected['predictions'])

    def test_missing_inputs_remain_unavailable(self):
        data = sample_snapshot()
        data["records"] = {}
        for name in (
            "cash_terms",
            "invoices",
            "classifications",
            "obligation_coverage",
            "principal_observations",
        ):
            data[name] = []
        result = self.run_model(data)
        self.assertIsNone(result["financial"]["score"]["value"])
        self.assertTrue(
            all(
                p["probabilities"] is None and p["reasons"]
                for p in result["predictions"].values()
            )
        )
        self.assertTrue(
            all(v is None for v in result["model_inputs"]["values"].values())
        )

    def test_invalid_payloads_reject_instead_of_silently_filtering(self):
        cases = []
        data = sample_snapshot()
        data["records"]["cash_movement"][0]["company_id"] = "someone-else"
        cases.append(data)
        data = sample_snapshot()
        data["records"]["cash_movement"][0]["amount_native"] = "NaN"
        cases.append(data)
        data = sample_snapshot()
        data["policy"] = {}
        cases.append(data)
        data = sample_snapshot()
        data["view"] = "as_of_simulation"
        cases.append(data)
        data = sample_snapshot()
        data["records"]["cash_movement"][1]["obligation_allocation_ids"] = ["alloc-2"]
        cases.append(data)
        data = sample_snapshot()
        data["invoices"].pop(0)
        cases.append(data)
        data = sample_snapshot()
        data["cash_terms"][0]["account_id"] = "orphan"
        cases.append(data)
        data = sample_snapshot()
        data["as_of"] = "2026-06-29"
        cases.append(data)
        data = sample_snapshot()
        data["as_of"] = "2027-01-31"
        cases.append(data)
        data = sample_snapshot()
        data["classifications"][0]["movement_id"] = "absent"
        cases.append(data)
        data = sample_snapshot()
        duplicate = copy.deepcopy(data["records"]["cash_movement"][0])
        duplicate["amount_native"] = "1"
        data["records"]["cash_movement"].append(duplicate)
        cases.append(data)
        for value in cases:
            with self.subTest(value=value.get("as_of")), self.assertRaises(InputError):
                self.run_model(value)
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', b"\xff", "[1,]"):
            with self.assertRaises(InputError) as caught:
                decode(raw)
            self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(InputError) as caught:
            decode(" " * (16 * 1024 * 1024 + 1))
        self.assertEqual(caught.exception.status, 413)

    def test_exclusions_and_early_dates_preserved(self):
        data = sample_snapshot()
        data["group_id"] = next(iter(self.bundle.excluded_group_ids))
        result = self.run_model(data)
        self.assertTrue(
            all(
                "original_benchmark_excluded" in p["reasons"]
                for p in result["predictions"].values()
            )
        )
        data["group_id"] = "new-group"
        data["as_of"] = "2025-12-31"
        result = self.run_model(data)
        self.assertIn(
            "before_selection_cutoff_no_backcast",
            result["predictions"]["receipt_contraction_3m"]["reasons"],
        )

    def test_isolated_release_requires_no_dataset_or_third_party_packages(self):
        code = """import json,sys
sys.path.insert(0, sys.argv[1])
from predictive_model.bundle import Bundle
from predictive_model.runtime import assess
bundle = Bundle.load(sys.argv[1] + '/bundle', expected_sha256=sys.argv[2])
value = assess(json.loads(sys.stdin.read()), bundle, observed_at='2027-01-01T00:00:00Z')
assert not {'numpy','duckdb','sklearn','joblib'} & sys.modules.keys()
print(json.dumps(value, allow_nan=False))
"""
        before = {
            p.relative_to(self.release).as_posix(): p.read_bytes()
            for p in self.release.rglob("*")
            if p.is_file()
        }
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                code,
                str(self.release),
                self.meta["bundle_sha256"],
            ],
            input=canonical_bytes(sample_snapshot()),
            capture_output=True,
            cwd=self.temp.name,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout), self.run_model())
        after = {
            p.relative_to(self.release).as_posix(): p.read_bytes()
            for p in self.release.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)

    def test_published_schemas(self):
        for name, schema in [
            ("request.schema.json", input_schema()),
            ("response.schema.json", output_schema()),
        ]:
            path = ROOT / "contracts/predictive" / name
            self.assertEqual(json.loads(path.read_text()), schema)
        if importlib.util.find_spec("jsonschema") is not None:
            jsonschema = __import__('jsonschema')
            jsonschema.validate(sample_snapshot(), input_schema())
            jsonschema.validate(self.run_model(), output_schema())


if __name__ == "__main__":
    unittest.main()
