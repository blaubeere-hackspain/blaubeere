from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "audit_financial_data.py"
_spec = importlib.util.spec_from_file_location("audit_financial_data", MODULE_PATH)
assert _spec and _spec.loader
_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_audit)


class InventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_root = ROOT / "tests" / "fixtures" / "financial_audit"
        self.tempdir = tempfile.TemporaryDirectory()
        self.input_root = Path(self.tempdir.name) / "data"
        self.output_root = Path(self.tempdir.name) / "reports"
        shutil.copytree(self.fixture_root / "materialized", self.input_root)
        (self.input_root / "invoices.csv").write_text(
            "version https://git-lfs.github.com/spec/v1\n"
            f"oid sha256:{'a' * 64}\n"
            "size 12345\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_materialized_csv_is_profiled_and_hashed(self) -> None:
        manifest = _audit.run_inventory(self.input_root, self.output_root, repository_root=ROOT)
        companies = next(item for item in manifest["sources"] if item["table"] == "companies")
        self.assertEqual(companies["status"], "materialized")
        self.assertTrue(companies["materialized"])
        self.assertEqual(companies["row_count"], 2)
        self.assertEqual(companies["header"], ["company_id", "group_id", "name"])
        expected = hashlib.sha256((self.input_root / "companies.csv").read_bytes()).hexdigest()
        self.assertEqual(companies["sha256"], expected)

    def test_lfs_pointer_is_unavailable_and_oid_not_content_hash(self) -> None:
        manifest = _audit.run_inventory(self.input_root, self.output_root, repository_root=ROOT)
        invoices = next(item for item in manifest["sources"] if item["table"] == "invoices")
        self.assertEqual(invoices["status"], "unavailable")
        self.assertFalse(invoices["materialized"])
        self.assertIsNone(invoices["sha256"])
        self.assertEqual(invoices["lfs_oid"], "a" * 64)
        self.assertEqual(invoices["lfs_declared_size"], 12345)
        self.assertEqual(manifest["status"], "blocked_by_data_access")

    def test_manifest_and_outputs_are_deterministic(self) -> None:
        before = {path: path.read_bytes() for path in self.input_root.rglob("*") if path.is_file()}
        first = _audit.run_inventory(self.input_root, self.output_root, repository_root=ROOT)
        first_outputs = {path.name: path.read_bytes() for path in self.output_root.iterdir()}
        second = _audit.run_inventory(self.input_root, self.output_root, repository_root=ROOT)
        second_outputs = {path.name: path.read_bytes() for path in self.output_root.iterdir()}
        self.assertEqual(first, second)
        self.assertEqual(first_outputs, second_outputs)
        self.assertEqual(before, {path: path.read_bytes() for path in self.input_root.rglob("*") if path.is_file()})

    def test_missing_auxiliary_sources_are_explicit(self) -> None:
        with chdir(self.tempdir.name):
            _audit.run_inventory(self.input_root, self.output_root, repository_root=Path(self.tempdir.name))
        manifest = json.loads((self.output_root / "manifest.json").read_text())
        statuses = {entry["path"]: entry["status"] for entry in manifest["auxiliary"]}
        self.assertEqual(statuses["data_dictionary.md"], "missing")
        self.assertEqual(statuses["dataset/output"], "missing")
        contract = json.loads((self.output_root / "schema_contract.json").read_text())
        self.assertEqual(contract["provisional_unit"], "company × closed month × currency")
        self.assertEqual(contract["contract_status"], "provisional")


if __name__ == "__main__":
    unittest.main()
