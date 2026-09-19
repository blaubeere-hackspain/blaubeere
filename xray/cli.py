"""CLI de xray: .venv/bin/python -m xray.cli <ingest|clean|quality>"""

import argparse
import os
import sys

from xray.contracts import CleanResult


def _print_results(title: str, results: dict[str, CleanResult]) -> None:
    print(f"\n{title}")
    print("-" * 72)
    print(f"{'tabla':<24}{'filas_in':>12}{'filas_out':>12}  flags")
    print("-" * 72)
    for name in sorted(results):
        r = results[name]
        flags = ", ".join(f"{k}={v}" for k, v in sorted(r.flag_counts.items()) if v)
        print(f"{name:<24}{r.rows_in:>12}{r.rows_out:>12}  {flags if flags else '-'}")
    print("-" * 72)


def _invariants_ok(results: dict[str, CleanResult]) -> bool:
    ok = True
    for name in sorted(results):
        r = results[name]
        try:
            r.assert_no_row_loss()
        except ValueError as e:
            print(f"INVARIANTE FALLIDA: {e}", file=sys.stderr)
            ok = False
    return ok


def cmd_ingest(_args: argparse.Namespace) -> int:
    from xray.ingest import run_ingest

    results = run_ingest()
    _print_results("INGESTA data/*.csv -> data/interim/*.parquet", results)
    return 0 if _invariants_ok(results) else 1


def cmd_clean(_args: argparse.Namespace) -> int:
    from xray.clean.dimensions import clean_dimensions
    from xray.clean.facts import clean_facts

    from xray.db import connect

    results: dict[str, CleanResult] = {}
    con = connect()
    try:
        results.update(clean_dimensions(con))
        results.update(clean_facts(con))
    finally:
        con.close()
    _print_results("LIMPIEZA data/interim/*.parquet -> data/clean/*.parquet", results)
    return 0 if _invariants_ok(results) else 1


def cmd_quality(_args: argparse.Namespace) -> int:
    from xray.db import connect
    from xray.quality import _build_report

    con = connect()
    try:
        report = _build_report(con)
    finally:
        con.close()

    cov = report["coverage"]
    print("\nCOMPROBACION DE CALIDAD (solo lectura, ejecucion activa)")
    print("-" * 72)
    mism = report["row_count_mismatches"]
    print(f"recuento de filas: {'OK' if not mism else 'MISMATCH: ' + ', '.join(mism)}")
    print(
        "cobertura de empresas "
        f"(total {cov['total_companies']}): "
        + ", ".join(f"{s}={d['companies']} ({d['pct']}%)" for s, d in cov["sources"].items())
    )
    h = report["history_months"]
    print(
        "meses cerrados por empresa: "
        f"min={h['min_closed_months']} p25={h['p25_closed_months']} "
        f"mediana={h['median_closed_months']} p75={h['p75_closed_months']} "
        f"max={h['max_closed_months']} "
        f"(<6 meses: {h['companies_lt_6_closed_months']} empresas)"
    )
    print("-" * 72)
    return 0 if not mism else 1


def cmd_build(_args):
    from xray.pipeline import run_build

    run_build()
    return 0


def cmd_pipeline(_args):
    from xray.clean.dimensions import clean_dimensions
    from xray.clean.facts import clean_facts
    from xray.db import connect
    from xray.flows import classify_flows
    from xray.ingest import ingest_all
    from xray.marts.cobro import build_panel_cobro
    from xray.marts.fx import build_fx_rates
    from xray.marts.observabilidad import build_observabilidad
    from xray.marts.flujos import build_panel_flujos
    from xray.marts.deuda import build_panel_deuda
    from xray.marts.evidencia import build_panel_evidencia
    from xray.marts.targets import build_targets_proxy
    from xray.quality import build_quality_report

    con = connect()
    try:
        for title, operation in [('INGESTA', ingest_all), ('DIMENSIONES', clean_dimensions),
                                 ('HECHOS', clean_facts)]:
            results = operation(con)
            _print_results(title, results)
            if not _invariants_ok(results):
                raise ValueError(f'Invariantes fallidas: {title}')
        for operation in (classify_flows, build_fx_rates, build_panel_cobro, build_observabilidad,
                          build_panel_flujos, build_panel_deuda, build_panel_evidencia, build_targets_proxy):
            result = operation(con)
            _print_results(result.table, {result.table: result})
        report = build_quality_report(con)
        if report['row_count_mismatches']:
            raise ValueError('El informe de calidad detecto perdida de filas')
    finally:
        con.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xray.cli", description="xray: ingesta y calidad")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ('build', 'ingest', 'clean', 'flows', 'marts'):
        sub.add_parser(command, help='reconstruccion completa validada, versionada y con copia previa').set_defaults(
            func=cmd_build
        )
    sub.add_parser('quality', help='comprobar calidad sin modificar artefactos').set_defaults(func=cmd_quality)
    sub.add_parser('_build', help=argparse.SUPPRESS).set_defaults(func=cmd_pipeline)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == '_build':
        from xray import paths
        if (not os.environ.get('XRAY_WORKSPACE') or paths.WORKSPACE == paths.ROOT
                or (paths.WORKSPACE / 'manifest.json').exists()):
            parser.error('_build requiere un workspace nuevo y aislado; usa build')
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
