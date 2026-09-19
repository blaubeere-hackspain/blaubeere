#!/usr/bin/env python3
"""Read-only inventory for financial audit inputs.

This module intentionally uses only Python's standard library.  It never fetches
Git LFS objects and never writes below the input root.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any

RAW_TABLES = (
    "balances",
    "banking_products",
    "companies",
    "debt_products",
    "debt_schedule_config",
    "groups",
    "invoices",
    "transactions",
)

LFS_VERSION = "version https://git-lfs.github.com/spec/v1"
LFS_OID_RE = re.compile(r"^oid sha256:([0-9a-f]{64})$")
LFS_SIZE_RE = re.compile(r"^size ([0-9]+)$")
NULL_MARKERS = {"", "null", "none", "na", "n/a"}


def _json_dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lfs_pointer(path: Path) -> dict[str, Any] | None:
    """Return pointer metadata, or None when path is materialized/non-pointer."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines or lines[0].strip() != LFS_VERSION:
        return None
    oid = None
    size = None
    for line in lines[1:]:
        if match := LFS_OID_RE.match(line.strip()):
            oid = match.group(1)
        elif match := LFS_SIZE_RE.match(line.strip()):
            try:
                size = int(match.group(1))
            except ValueError:
                size = None
    return {"lfs_oid": oid, "lfs_declared_size": size}


def _infer_type(value: str) -> str:
    text = value.strip()
    if text.lower() in NULL_MARKERS:
        return "null"
    try:
        int(text)
        return "integer"
    except ValueError:
        try:
            float(text)
            return "number"
        except ValueError:
            return "string"


def _csv_metadata(path: Path) -> dict[str, Any]:
    """Read deterministic, bounded metadata from a materialized CSV."""
    result: dict[str, Any] = {
        "encoding": "utf-8",
        "delimiter": None,
        "header": [],
        "schema": [],
        "row_count": 0,
        "malformed_rows": 0,
    }
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|") if sample.strip() else csv.excel
        except csv.Error:
            dialect = csv.excel
        result["delimiter"] = dialect.delimiter
        reader = csv.reader(handle, dialect)
        try:
            header = next(reader)
        except StopIteration:
            return result
        result["header"] = [column.strip() for column in header]
        types = [set() for _ in header]
        for row in reader:
            result["row_count"] += 1
            if len(row) != len(header):
                result["malformed_rows"] += 1
            for index, value in enumerate(row[: len(header)]):
                types[index].add(_infer_type(value))
        schema = []
        for name, observed in zip(result["header"], types, strict=True):
            observed.discard("null")
            if not observed:
                inferred = "null"
            elif len(observed) == 1:
                inferred = next(iter(observed))
            else:
                inferred = "mixed"
            schema.append({"name": name, "inferred_type": inferred})
        result["schema"] = schema
    return result


def _base_entry(table: str, relative: str, input_root: Path, *, kind: str) -> dict[str, Any]:
    return {
        "table": table,
        "path": (input_root / relative).as_posix(),
        "relative_path": relative,
        "kind": kind,
        "status": "missing",
        "available": False,
        "materialized": False,
        "sha256": None,
        "lfs_oid": None,
        "lfs_declared_size": None,
        "byte_size": None,
        "encoding": None,
        "delimiter": None,
        "header": [],
        "schema": [],
        "row_count": None,
        "malformed_rows": None,
        "extraction_date": None,
        "availability_date": None,
        "known_at": None,
    }


def _inventory_file(table: str, relative: str, input_root: Path, *, kind: str) -> dict[str, Any]:
    entry = _base_entry(table, relative, input_root, kind=kind)
    path = input_root / relative
    if not path.is_file():
        return entry
    pointer = _lfs_pointer(path)
    if pointer is not None:
        entry.update(status="unavailable", **pointer)
        return entry
    entry.update(
        status="materialized",
        available=True,
        materialized=True,
        sha256=_sha256(path),
        byte_size=path.stat().st_size,
    )
    if path.suffix.lower() == ".csv":
        try:
            entry.update(_csv_metadata(path))
        except (OSError, UnicodeError, csv.Error) as exc:
            entry.update(status="unreadable", available=False, materialized=False)
            entry["error"] = type(exc).__name__
    return entry


def _display_auxiliary_status(input_root: Path, relative: str, repository_root: Path) -> dict[str, Any]:
    candidates = (input_root.parent / relative, repository_root / relative, Path.cwd() / relative)
    found = next((candidate for candidate in candidates if candidate.exists()), None)
    return {
        "path": relative,
        "status": "available" if found is not None else "missing",
        "available": found is not None,
    }


def _schema_contract(input_root: Path) -> dict[str, Any]:
    tables = []
    for table in RAW_TABLES:
        tables.append(
            {
                "table": table,
                "path": (input_root / f"{table}.csv").as_posix(),
                "schema_status": "pending_until_materialized",
                "columns": [],
                "primary_key": {"status": "pending", "columns": []},
                "relationships": [],
            }
        )
    return {
        "contract_version": "1.0.0",
        "contract_status": "provisional",
        "provisional_unit": "company × closed month × currency",
        "tables": tables,
        "relationship_policy": "Do not infer foreign keys or economic event semantics until source schema is available and reviewed.",
        "availability_policy": "Missing and Git LFS pointer files are unavailable, never zero rows.",
        "open_questions": [
            "Extraction date, availability date, and historical known_at timestamps are unavailable.",
            "Invoice, payment, due-date, status, FX, and balance semantics require owner confirmation.",
            "License and permitted use of financial source data require owner confirmation.",
        ],
    }


def _markdown(manifest: dict[str, Any], contract: dict[str, Any]) -> str:
    lines = [
        "# Financial data inventory",
        "",
        f"- Status: `{manifest['status']}`",
        f"- Input root: `{manifest['input_root']}`",
        "- Mode: read-only; Git LFS objects were not fetched.",
        f"- Provisional unit: `{manifest['provisional_unit']}`",
        "",
        "## Raw logical tables",
        "",
        "| Table | Path | Status | Materialized | Rows | SHA-256 | LFS OID |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for item in manifest["sources"]:
        rows = "unknown" if item["row_count"] is None else str(item["row_count"])
        digest = item["sha256"] or "—"
        oid = item["lfs_oid"] or "—"
        lines.append(f"| `{item['table']}` | `{item['path']}` | `{item['status']}` | {str(item['materialized']).lower()} | {rows} | `{digest}` | `{oid}` |")
    lines += ["", "## Derived/clean artifacts", "", "| Path | Status | Materialized | SHA-256 |", "| --- | --- | ---: | --- |"]
    for item in manifest["derived_artifacts"]:
        lines.append(f"| `{item['path']}` | `{item['status']}` | {str(item['materialized']).lower()} | `{item['sha256'] or '—'}` |")
    lines += ["", "## Auxiliary sources", "", "| Path | Status |", "| --- | --- |"]
    for item in manifest["auxiliary"]:
        lines.append(f"| `{item['path']}` | `{item['status']}` |")
    lines += ["", "## Contract and unresolved semantics", "", f"- Contract version: `{contract['contract_version']}`."]
    for question in contract["open_questions"]:
        lines.append(f"- {question}")
    lines += ["", "## Reproduction", "", "```text", f"python3 scripts/audit_financial_data.py inventory --input-root {manifest['input_root']} --output-dir reports/readiness", "```", ""]
    return "\n".join(lines)


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return ()
    return (path for path in sorted(root.rglob("*")) if path.is_file())


def run_inventory(input_root: Path, output_dir: Path, repository_root: Path | None = None) -> dict[str, Any]:
    input_root = input_root.resolve()
    output_dir = output_dir.resolve()
    repository_root = (repository_root or Path(__file__).resolve().parents[1]).resolve()
    sources = [_inventory_file(table, f"{table}.csv", input_root, kind="raw") for table in RAW_TABLES]
    derived = [_inventory_file(table, f"clean/{table}.parquet", input_root, kind="derived_clean") for table in RAW_TABLES]
    known = {Path(item["relative_path"]) for item in sources + derived}
    additional = []
    for path in _iter_files(input_root):
        relative = path.relative_to(input_root)
        if relative not in known:
            additional.append(_inventory_file(relative.stem, relative.as_posix(), input_root, kind="additional"))
    auxiliary = [
        _display_auxiliary_status(input_root, "data_dictionary.md", repository_root),
        _display_auxiliary_status(input_root, "dataset/output", repository_root),
    ]
    unavailable = [item for item in sources if item["status"] in {"unavailable", "missing", "unreadable"}]
    if any(item["status"] == "unavailable" for item in sources):
        status = "blocked_by_data_access"
    elif unavailable:
        status = "incomplete"
    else:
        status = "available"
    display_input = input_root.as_posix()
    # Avoid host-specific absolute paths in normal repository output.
    with suppress(ValueError):
        display_input = input_root.relative_to(repository_root).as_posix()
    for item in sources + derived + additional:
        item["path"] = f"{display_input}/{item['relative_path']}"
    contract = _schema_contract(input_root)
    for table in contract["tables"]:
        table["path"] = f"{display_input}/{table['path'].split('/')[-1]}"
    manifest = {
        "manifest_version": "1.0.0",
        "status": status,
        "input_root": display_input,
        "provisional_unit": "company × closed month × currency",
        "sources": sources,
        "derived_artifacts": derived,
        "additional_files": additional,
        "files": sources + derived + additional,
        "auxiliary": auxiliary,
        "raw_inputs_preserved": True,
        "hash_policy": "sha256 is recorded only for materialized files; Git LFS OIDs are recorded separately and are not content hashes.",
    }
    _json_dump(output_dir / "manifest.json", manifest)
    _json_dump(output_dir / "schema_contract.json", contract)
    (output_dir / "inventory.md").parent.mkdir(parents=True, exist_ok=True)
    (output_dir / "inventory.md").write_text(_markdown(manifest, contract), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventory financial audit inputs without modifying raw data.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser("inventory", help="inventory source and derived input files")
    inventory.add_argument("--input-root", type=Path, default=Path("data"))
    inventory.add_argument("--output-dir", type=Path, default=Path("reports/readiness"))
    args = parser.parse_args(argv)
    if args.command == "inventory":
        run_inventory(args.input_root, args.output_dir)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
