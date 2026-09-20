"""Tests de xray.collection_schedule (lado COBROS).

Todos los tests son SINTETICOS: no dependen de que existan
``data/clean/invoices.parquet`` ni ``reports/current.json`` (ambos gitignored en
un worktree recien creado). La fuente real se valida fuera de esta suite.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from xray import collection_schedule as cs

C = date(2026, 8, 31)
EPOCH = date(1970, 1, 1)


def _d(offset: int) -> int:
    """Dia (desde 1970) desplazado ``offset`` dias respecto al corte."""
    return (C + timedelta(days=offset) - EPOCH).days


def make_frame(rows: list[tuple]) -> pd.DataFrame:
    """DataFrame normalizado como el que produce ``load_invoices``."""
    frame = pd.DataFrame(
        rows,
        columns=["company_id", "issuance", "due", "payment", "amount_eur", "status"],
    )
    for col in ("issuance", "due", "payment", "amount_eur"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    frame["company_id"] = frame["company_id"].astype("string")
    frame["status"] = frame["status"].astype("string")
    return frame


def rich_frame() -> pd.DataFrame:
    """Cartera sintetica con pagos previos, mora, no vencidas y no valorables."""
    rows: list[tuple] = []
    # Empresa A: 12 pagadas antes del corte (retraso propio + cohorte de cura).
    for i in range(12):
        due = _d(-200 - i * 10)
        rows.append(("A", _d(-260 - i * 10), due, due + 5, 100.0, "paid"))
    # Empresa B: solo 2 pagadas antes del corte -> muestra corta.
    for i in range(2):
        due = _d(-200 - i * 10)
        rows.append(("B", _d(-260 - i * 10), due, due + 7, 100.0, "paid"))
    # Viva A vencida: 'overdue' con payment_date = due_date (fecha PROYECTADA).
    rows.append(("A", _d(-40), _d(-10), _d(-10), 1000.0, "overdue"))
    # Viva A aun no vencida.
    rows.append(("A", _d(-10), _d(50), None, 2000.0, "pending"))
    # Viva B pagada DESPUES del corte (sigue viva en el corte).
    rows.append(("B", _d(-30), _d(-5), _d(20), 500.0, "paid"))
    # Factura emitida DESPUES del corte: no existe en el corte.
    rows.append(("A", _d(5), _d(60), None, 9999.0, "pending"))
    # Cancelada: fuera del universo por la regla 1.
    rows.append(("A", _d(-50), _d(-20), None, 777.0, "cancel"))
    # Viva B sin importe (NO es cero).
    rows.append(("B", _d(-20), _d(-3), _d(-3), None, "overdue"))
    # Viva B sin fecha de vencimiento (no se puede situar): importe conocido.
    rows.append(("B", _d(-20), None, None, 300.0, "pending"))
    return make_frame(rows)


# --------------------------------------------------------------------------- #
# (a) NO-FUGA
# --------------------------------------------------------------------------- #
def test_no_leak_post_cut_payments_and_invoices_do_not_change_cut():
    """Eliminar/alterar hechos posteriores al corte no cambia NINGUNA cifra."""
    base = rich_frame()
    res_base = cs.compute_cut(base, C)

    # Version mutilada: fuera facturas emitidas despues del corte; los pagos
    # posteriores al corte se llevan muy lejos (siguen siendo futuros).
    mut = base[base["issuance"] <= _d(0)].copy()
    futuro = mut["payment"].notna() & (mut["payment"] > _d(0))
    mut.loc[futuro, "payment"] = _d(5000)

    res_mut = cs.compute_cut(mut, C)

    pd.testing.assert_frame_equal(
        res_base.calendario.reset_index(drop=True),
        res_mut.calendario.reset_index(drop=True),
    )
    pd.testing.assert_frame_equal(
        res_base.horizontes.reset_index(drop=True),
        res_mut.horizontes.reset_index(drop=True),
    )
    censo_base = {k: v for k, v in res_base.censo.items() if k != "n_facturas"}
    censo_mut = {k: v for k, v in res_mut.censo.items() if k != "n_facturas"}
    assert censo_base == censo_mut


def test_no_leak_delay_and_probability_use_only_payments_before_cut():
    """Un pago posterior al corte no entra en retraso ni en probabilidad."""
    rows = [
        ("A", _d(-300), _d(-200), _d(-195), 100.0, "paid"),
        # Pago posterior al corte: si se colara, moveria el retraso mediano.
        ("A", _d(-100), _d(-50), _d(400), 100.0, "paid"),
        ("A", _d(-40), _d(-10), _d(-10), 1000.0, "overdue"),
    ]
    base = make_frame(rows)
    res = cs.compute_cut(base, C)
    # Solo hay 1 pago observable (retraso 5) -> mediana global 5.
    assert res.censo["retraso_mediano_global_dias"] == pytest.approx(5.0)
    # Y la factura viva se programa en corte+1 (due+5 ya esta en el pasado).
    cal = res.calendario
    assert (cal["dia"] == C + timedelta(days=1)).all()


# --------------------------------------------------------------------------- #
# (b) CRITERIO STATUS-AWARE
# --------------------------------------------------------------------------- #
def test_overdue_with_projected_payment_date_is_alive():
    """'overdue' con payment_date_ok = due_date_ok <= corte sigue VIVA."""
    frame = make_frame(
        [("A", _d(-40), _d(-10), _d(-10), 1000.0, "overdue")]
    )
    res = cs.compute_cut(frame, C)
    assert res.censo["n_vivas"] == 1
    assert res.censo["n_vencidas"] == 1
    # No se ha tratado como pagada: aparece en el calendario.
    assert not res.calendario.empty
    assert res.horizontes[res.horizontes["h"] == 30]["n_facturas_vivas"].iloc[0] == 1


def test_paid_after_cut_stays_alive_and_paid_before_cut_does_not():
    """El estado terminal 'paid' solo cuenta como pago si ocurrio <= corte."""
    frame = make_frame(
        [
            ("A", _d(-40), _d(-10), _d(-10), 100.0, "paid"),  # pagada antes
            ("A", _d(-40), _d(-5), _d(5), 200.0, "paid"),  # pagada despues
        ]
    )
    res = cs.compute_cut(frame, C)
    assert res.censo["n_vivas"] == 1
    assert res.censo["importe_vivo_eur"] == pytest.approx(200.0)


# --------------------------------------------------------------------------- #
# Nulo no es cero y motivos de no valorable
# --------------------------------------------------------------------------- #
def test_null_amount_is_not_zero():
    frame = make_frame(
        [
            ("A", _d(-40), _d(-10), None, None, "pending"),
            ("A", _d(-40), _d(-5), None, 100.0, "pending"),
        ]
    )
    res = cs.compute_cut(frame, C)
    assert res.censo["n_vivas"] == 2
    assert res.censo["n_no_valorable"] == 1
    # El importe vivo solo suma lo valorable; el nulo NO se imputa como cero.
    assert res.censo["importe_vivo_eur"] == pytest.approx(100.0)
    assert "sin_importe_eur" in res.censo["motivos_no_valorable"]


def test_missing_due_date_is_non_valuable_with_its_amount():
    frame = make_frame(
        [("A", _d(-40), None, None, 300.0, "pending")]
    )
    res = cs.compute_cut(frame, C)
    assert res.censo["n_vivas"] == 1
    assert res.censo["n_no_valorable"] == 1
    assert res.censo["importe_no_valorable_eur"] == pytest.approx(300.0)
    assert res.censo["motivos_no_valorable"]["sin_fecha_vencimiento"]["eur"] == pytest.approx(300.0)
    assert res.calendario.empty


def test_cancel_excluded_by_default_and_recoverable():
    frame = make_frame(
        [
            ("A", _d(-50), _d(-20), None, 777.0, "cancel"),
            ("A", _d(-40), _d(-10), None, 100.0, "pending"),
        ]
    )
    excl = cs.compute_cut(frame, C, excluir_cancel=True)
    incl = cs.compute_cut(frame, C, excluir_cancel=False)
    assert excl.censo["n_vivas"] == 1
    assert incl.censo["n_vivas"] == 2


# --------------------------------------------------------------------------- #
# Coherencia calendario <-> horizonte y tabla de probabilidad
# --------------------------------------------------------------------------- #
def test_calendar_sums_match_horizons_per_company():
    res = cs.compute_cut(rich_frame(), C)
    cal = res.calendario
    for _, row in res.horizontes.iterrows():
        h = int(row["h"])
        limite = C + timedelta(days=h)
        sub = cal[(cal["company_id"] == row["company_id"]) & (cal["dia"] <= limite)]
        assert sub["importe_esperado_eur"].sum() == pytest.approx(
            row["importe_esperado_eur"]
        )


def test_probability_table_is_defined_and_non_increasing():
    res = cs.compute_cut(rich_frame(), C)
    tabla = {r["tramo"]: r["prob_cobro"] for r in res.censo["tabla_probabilidad"]}
    assert set(tabla) == {cs.BUCKET_NO_VENCIDA, *cs.BUCKET_LABELS}
    for lo, hi in zip(cs.BUCKET_LABELS[:-1], cs.BUCKET_LABELS[1:]):
        assert tabla[hi] <= tabla[lo] + 1e-12
    assert all(0.0 <= v <= 1.0 for v in tabla.values())


def test_sin_due_is_not_assigned_to_a_bucket():
    res = cs.compute_cut(rich_frame(), C)
    # La factura sin vencimiento (300 EUR) no aparece en ningun tramo valorable.
    total_tramos = sum(r["importe_vivo_eur"] for r in res.censo["tabla_probabilidad"])
    assert total_tramos == pytest.approx(
        res.censo["importe_vivo_eur"] - 300.0
    )


# --------------------------------------------------------------------------- #
# Cortes y CLI
# --------------------------------------------------------------------------- #
def test_build_cortes_matches_cashflow_origins_and_includes_live_cut():
    cortes = cs.build_cortes()
    assert cortes[0] == date(2024, 11, 30)
    assert cortes[-1] == C
    assert len(cortes) == len(set(cortes))
    assert all(d.day in (28, 29, 30, 31) for d in cortes)


def _write_source(frame: pd.DataFrame, path) -> None:
    """Escribe un parquet con el esquema crudo que lee ``load_invoices``."""
    raw = pd.DataFrame(
        {
            "company_id": frame["company_id"].astype(str),
            "issuance_date_ok": pd.to_datetime(
                [_epoch_to_ts(x) for x in frame["issuance"]]
            ),
            "due_date_ok": pd.to_datetime([_epoch_to_ts(x) for x in frame["due"]]),
            "payment_date_ok": pd.to_datetime(
                [_epoch_to_ts(x) for x in frame["payment"]]
            ),
            "amount_eur": frame["amount_eur"],
            "status_norm": frame["status"].astype(str),
            "flow_side": "inflow",
        }
    )
    raw.to_parquet(path, index=False)


def _epoch_to_ts(days):
    if days is None or pd.isna(days):
        return pd.NaT
    return pd.Timestamp(EPOCH + timedelta(days=int(days)))


def test_cli_writes_four_artifacts_and_is_reproducible(tmp_path):
    src = tmp_path / "invoices.parquet"
    _write_source(rich_frame(), src)
    out = tmp_path / "out"
    assert cs.main(["--invoices", str(src), "--output-dir", str(out)]) == 0
    names = ["calendario.parquet", "horizontes.parquet", "censo.json", "report.md"]
    for name in names:
        assert (out / name).exists(), name
    first = {name: (out / name).read_bytes() for name in names}
    assert cs.main(["--invoices", str(src), "--output-dir", str(out)]) == 0
    for name in names:
        assert (out / name).read_bytes() == first[name], name
    # El censo publicado lleva el corte vivo.
    import json

    censo = json.loads((out / "censo.json").read_text())
    assert censo["corte_vivo"] == C.isoformat()
    assert C.isoformat() in censo["cortes"]
