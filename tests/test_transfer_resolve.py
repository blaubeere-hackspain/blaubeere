import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from xray import paths
from xray.transfer_resolve import (
    AMOUNT_TOLERANCE_EUR, DESC_PATTERNS, R1_RULE, R2_LAX_RULE, R2_STRICT_RULE,
    R3_EFECTIVO_RULE, R3_PASARELA_RULE, R3_TERCERO_RULE, R3_TRASPASO_RULE,
    compute_coverage, main, normalize_description, resolve_transfers,
)
import pandas as pd


def base_row(**changes):
    row = {
        'transaction_id': 't1', 'company_id': 'A', 'product_id': 'p1',
        'date_ok': date(2025, 1, 15), 'month': '2025-01-01', 'amount': 100.0,
        'amount_eur': 100.0, 'fx_ambiguous': False, 'category_norm': 'transfer',
        'direction': 'in', 'counterparty_id': None,
    }
    return dict(row, **changes)


def pair(in_id, out_id, *, company='A', in_product='p1', out_product='p2',
         in_eur=100.0, out_eur=-100.0, day_offset=2, in_cp=None, out_cp=None,
         category='transfer', fx=False, eur_null=False):
    day = date(2025, 1, 15)
    incoming = base_row(transaction_id=in_id, product_id=in_product, date_ok=day,
                        amount=in_eur, amount_eur=0.0 if eur_null else in_eur,
                        direction='in', counterparty_id=in_cp, category_norm=category,
                        fx_ambiguous=fx)
    outgoing = base_row(transaction_id=out_id, product_id=out_product,
                        date_ok=day + timedelta(days=day_offset), amount=out_eur,
                        amount_eur=0.0 if eur_null else out_eur, direction='out',
                        counterparty_id=out_cp, category_norm=category, fx_ambiguous=fx)
    return [incoming, outgoing]


def run_main(argv):
    with contextlib.redirect_stdout(io.StringIO()) as stream:
        main(argv)
    return stream.getvalue()


def by_id(results):
    return {row['transaction_id']: row for row in results}


class TransferResolveTest(unittest.TestCase):
    def test_same_company_equal_amount_two_days_is_strict(self):
        results = by_id(resolve_transfers(pair('in', 'out', day_offset=2)))
        self.assertEqual(results['in']['resolved_class'], 'internal_transfer')
        self.assertEqual(results['in']['rule'], R2_STRICT_RULE)
        self.assertEqual(results['in']['confidence'], 'alta')
        self.assertEqual(results['in']['matched_with'], 'out')
        self.assertEqual(results['out']['matched_with'], 'in')

    def test_five_days_is_not_strict_but_lax(self):
        results = by_id(resolve_transfers(pair('in', 'out', day_offset=5)))
        self.assertEqual(results['in']['rule'], R2_LAX_RULE)
        self.assertEqual(results['in']['confidence'], 'baja')
        self.assertEqual(results['out']['resolved_class'], 'internal_transfer')

    def test_one_out_two_candidates_pairs_once_and_is_deterministic(self):
        rows = pair('in1', 'out', day_offset=1)
        rows += [base_row(transaction_id='in2', product_id='p3', date_ok=date(2025, 1, 16),
                          amount=100.0, amount_eur=100.0, direction='in')]
        first = resolve_transfers(rows)
        second = resolve_transfers(rows)
        self.assertEqual(first, second)
        matched = by_id(first)
        paired = [row for row in matched.values() if row['resolved_class'] == 'internal_transfer']
        self.assertEqual(len(paired), 2)
        out = matched['out']
        self.assertIn(out['matched_with'], ('in1', 'in2'))
        other = 'in2' if out['matched_with'] == 'in1' else 'in1'
        self.assertEqual(matched[other]['resolved_class'], 'ambiguo')
        self.assertIsNone(matched[other]['matched_with'])
        self.assertEqual(len({row['matched_with'] for row in paired}), 2)

    def test_fx_ambiguous_or_null_eur_never_paired(self):
        results = by_id(resolve_transfers(pair('in', 'out', fx=True, eur_null=True)))
        for key in ('in', 'out'):
            self.assertEqual(results[key]['resolved_class'], 'ambiguo')
            self.assertEqual(results[key]['confidence'], 'ninguna')
            self.assertIsNone(results[key]['matched_with'])
        results = by_id(resolve_transfers(pair('in', 'out', eur_null=True)))
        for key in ('in', 'out'):
            self.assertEqual(results[key]['resolved_class'], 'ambiguo')

    def test_cash_withdrawals_leave_transfer_bucket_as_operating_out(self):
        rows = [base_row(transaction_id='cash', category_norm='cash_withdrawal',
                         amount=-50.0, amount_eur=-50.0, direction='out'),
                base_row(transaction_id='pos', category_norm='pos_withdrawal',
                         amount=-30.0, amount_eur=-30.0, direction='out')]
        results = by_id(resolve_transfers(rows))
        for key in ('cash', 'pos'):
            self.assertEqual(results[key]['resolved_class'], 'operating_out')
            self.assertEqual(results[key]['rule'], R1_RULE)
            self.assertEqual(results[key]['confidence'], 'media')
            self.assertIsNone(results[key]['matched_with'])

    def test_contradictory_counterparty_blocks_pairing(self):
        results = by_id(resolve_transfers(pair('in', 'out', in_cp='C1', out_cp='C2')))
        for key in ('in', 'out'):
            self.assertEqual(results[key]['resolved_class'], 'ambiguo')
        ok = by_id(resolve_transfers(pair('in', 'out', in_cp='C1')))
        self.assertEqual(ok['in']['resolved_class'], 'internal_transfer')

    def test_pure_function_does_not_mutate_input(self):
        rows = pair('in', 'out')
        snapshot = [dict(row) for row in rows]
        resolve_transfers(rows)
        self.assertEqual(rows, snapshot)

    def test_result_covers_every_row_once(self):
        rows = pair('in', 'out') + [
            base_row(transaction_id='cash', category_norm='cash_withdrawal',
                     amount=-10.0, amount_eur=-10.0, direction='out'),
            base_row(transaction_id='solo', product_id='p9', date_ok=date(2025, 1, 20),
                     amount=70.0, amount_eur=70.0, direction='in'),
        ]
        results = resolve_transfers(rows)
        self.assertEqual(len(results), len(rows))
        self.assertEqual({row['transaction_id'] for row in results}, {row['transaction_id'] for row in rows})

    def test_tolerance_is_declared_and_respected(self):
        rows = pair('in', 'out', in_eur=100.0, out_eur=-100.0 - AMOUNT_TOLERANCE_EUR)
        self.assertEqual(by_id(resolve_transfers(rows))['in']['rule'], R2_STRICT_RULE)
        rows = pair('in', 'out', in_eur=100.0, out_eur=-101.0)
        self.assertEqual(by_id(resolve_transfers(rows))['in']['resolved_class'], 'ambiguo')

    def test_cli_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'transfer_resolution'
            output.mkdir()
            (output / 'coverage.json').write_text('{"existing": true}')
            with self.assertRaises(SystemExit):
                run_main(['--output', str(output)])
            self.assertEqual(json.loads((output / 'coverage.json').read_text()), {'existing': True})
            self.assertFalse((output / 'resolution.parquet').exists())

    @unittest.skipUnless(paths.CLEAN_DIR.exists() and paths.MARTS_DIR.exists(),
                         'Workspace publicado no disponible')
    def test_cli_does_not_touch_source_artifacts(self):
        watched = [directory for directory in (paths.CLEAN_DIR, paths.INTERIM_DIR, paths.MARTS_DIR)
                   if directory.exists()]

        def digests():
            result = {}
            for directory in watched:
                for file in sorted(directory.glob('*.parquet')):
                    with file.open('rb') as stream:
                        result[str(file)] = hashlib.file_digest(stream, 'sha256').hexdigest()
            return result

        before = digests()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'transfer_resolution'
            run_main(['--output', str(output)])
            self.assertTrue((output / 'resolution.parquet').exists())
            coverage = json.loads((output / 'coverage.json').read_text())
            self.assertIn('ambiguo_antes', coverage)
        self.assertEqual(digests(), before)

    def test_coverage_reports_zeroed_company_months_and_residual(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'transfer_resolution'
            run_main(['--output', str(output)])
            coverage = json.loads((output / 'coverage.json').read_text())
            self.assertGreater(coverage['empresa_mes_a_cero']['total_empresa_mes'], 0)
            self.assertGreaterEqual(coverage['empresa_mes_a_cero']['count'], 0)
            self.assertIn('falsos_pares', coverage['honestidad'])
            self.assertEqual(coverage['ambiguo_antes']['rows'],
                             coverage['recuperado']['r1_cash_withdrawal']['rows']
                             + coverage['recuperado']['r2_estricta']['rows']
                             + coverage['recuperado']['r2_laxa']['rows']
                             + coverage['recuperado']['r3_descripcion']['rows']
                             + coverage['ambiguo_despues']['rows'])
        # fin: ninguna de las cotas se presenta como verdad
        bounds = coverage['cotas_volumen_interno']
        self.assertLessEqual(bounds['cota_inferior_estricta']['eur'],
                             bounds['cota_superior_estricta_mas_laxa']['eur'])

    def test_compute_coverage_flags_and_distribution(self):
        rows = pd.DataFrame([
            base_row(transaction_id='in', date_ok=date(2025, 1, 15)),
            base_row(transaction_id='out', product_id='p2', date_ok=date(2025, 1, 17), amount=-100.0,
                     amount_eur=-100.0, direction='out'),
            base_row(transaction_id='cash', category_norm='cash_withdrawal',
                     date_ok=date(2025, 1, 18), amount=-40.0, amount_eur=-40.0, direction='out'),
            base_row(transaction_id='left', product_id='p9', date_ok=date(2025, 1, 20),
                     amount=25.0, amount_eur=25.0, direction='in'),
        ])
        results = pd.DataFrame(resolve_transfers(rows.to_dict('records')))
        coverage = compute_coverage(rows, results)
        self.assertEqual(coverage['ambiguo_antes']['rows'], 4)
        self.assertEqual(coverage['ambiguo_antes']['eur'], 265.0)
        self.assertEqual(coverage['recuperado']['r1_cash_withdrawal']['rows'], 1)
        self.assertEqual(coverage['recuperado']['r2_estricta']['rows'], 2)
        self.assertEqual(coverage['ambiguo_despues']['rows'], 1)
        self.assertEqual(coverage['empresa_mes_a_cero']['count'], 0)
        self.assertEqual(coverage['residual_pct_empresa_mes']['mediana'], round(25.0 * 100.0 / 265.0, 2))


class R3DescripcionTest(unittest.TestCase):
    @staticmethod
    def solo(desc, **changes):
        return [base_row(transaction_id='solo', description=desc,
                         amount=-25.0, amount_eur=-25.0, direction='out', **changes)]

    def test_par_r2_manda_sobre_el_texto(self):
        rows = pair('in', 'out')
        rows[0]['description'] = 'TRASPASO A CTA 1234'
        rows[1]['description'] = 'TRANSFERENCIA A PROVEEDOR'
        results = by_id(resolve_transfers(rows))
        for key in ('in', 'out'):
            self.assertEqual(results[key]['resolved_class'], 'internal_transfer')
            self.assertEqual(results[key]['rule'], R2_STRICT_RULE)
            self.assertEqual(results[key]['confidence'], 'alta')
            self.assertIsNone(results[key]['pattern'])

    def test_traspaso_sin_par_es_media_y_distinguible_de_r2(self):
        results = by_id(resolve_transfers(self.solo('TRASPASO A CTA 1234')))
        self.assertEqual(results['solo']['resolved_class'], 'internal_transfer')
        self.assertEqual(results['solo']['rule'], R3_TRASPASO_RULE)
        self.assertEqual(results['solo']['confidence'], 'media')
        self.assertIsNone(results['solo']['matched_with'])
        # distinguible de un par R2 demostrado: otra regla y otra confianza
        paired = by_id(resolve_transfers(pair('in', 'out')))['in']
        self.assertNotEqual(paired['rule'], results['solo']['rule'])
        self.assertNotEqual(paired['confidence'], results['solo']['confidence'])

    def test_stripe_payout_es_operating_in(self):
        results = by_id(resolve_transfers(self.solo('STRIPE PAYOUT')))
        self.assertEqual(results['solo']['resolved_class'], 'operating_in')
        self.assertEqual(results['solo']['rule'], R3_PASARELA_RULE)
        self.assertEqual(results['solo']['confidence'], 'media')

    def test_disposicion_efectivo_es_operating_out(self):
        results = by_id(resolve_transfers(self.solo('DISP.ENTREG.EFECT.')))
        self.assertEqual(results['solo']['resolved_class'], 'operating_out')
        self.assertEqual(results['solo']['rule'], R3_EFECTIVO_RULE)
        self.assertEqual(results['solo']['confidence'], 'media')
        results = by_id(resolve_transfers(self.solo('Retirada de fondos')))
        self.assertEqual(results['solo']['resolved_class'], 'operating_out')

    def test_transferencia_tercero_sigue_el_signo(self):
        rows = [base_row(transaction_id='sale', description='TRANSFERENCIA A PROVEEDOR',
                         date_ok=date(2025, 1, 10), amount=-80.0, amount_eur=-80.0, direction='out'),
                base_row(transaction_id='entra', description='TRANSFERENCIA A PROVEEDOR',
                         date_ok=date(2025, 6, 1), amount=80.0, amount_eur=80.0, direction='in')]
        results = by_id(resolve_transfers(rows))
        self.assertEqual(results['sale']['resolved_class'], 'operating_out')
        self.assertEqual(results['sale']['confidence'], 'baja')
        self.assertEqual(results['sale']['rule'], R3_TERCERO_RULE)
        self.assertEqual(results['entra']['resolved_class'], 'operating_in')
        # fx_ambiguous con signo: la clase se asigna igualmente
        fx = by_id(resolve_transfers([base_row(transaction_id='fx', description='TRANSFERENCIA A PROVEEDOR',
                                               amount=-80.0, amount_eur=-80.0, fx_ambiguous=True)]))
        self.assertEqual(fx['fx']['resolved_class'], 'operating_out')
        # sin signo utilizable no se fuerza
        nulo = by_id(resolve_transfers([base_row(transaction_id='nulo', description='TRANSFERENCIA A PROVEEDOR',
                                                 amount=None, amount_eur=None)]))
        self.assertEqual(nulo['nulo']['resolved_class'], 'ambiguo')
        self.assertEqual(nulo['nulo']['confidence'], 'ninguna')

    def test_vacia_o_desconocida_sigue_ambigua(self):
        for desc in (None, '', '   ', 'CONCEPTO SIN PATRON CONOCIDO'):
            results = by_id(resolve_transfers(self.solo(desc)))
            self.assertEqual(results['solo']['resolved_class'], 'ambiguo', desc)
            self.assertEqual(results['solo']['confidence'], 'ninguna', desc)
            self.assertEqual(results['solo']['rule'], None)

    def test_normalizacion_no_cambia_la_clasificacion(self):
        variantes = ['traspaso a cta 1234', 'TRÁSPASO A CTA 1234', 'Traspaso   a CTA 1234 ']
        clases = {by_id(resolve_transfers(self.solo(desc)))['solo']['resolved_class'] for desc in variantes}
        self.assertEqual(clases, {'internal_transfer'})
        self.assertEqual(normalize_description('  Traspaso   AÑO  Cta '), 'TRASPASO ANO CTA')

    def test_primera_coincidencia_gana_y_nadie_es_tocado_dos_veces(self):
        # 'TRASPASO TRANSFERENCIAS' casa ^TRASPASO y tambien contendria TRANSF:
        # el primero en el orden declarado gana.
        results = by_id(resolve_transfers(self.solo('TRASPASO TRANSFERENCIAS')))
        self.assertEqual(results['solo']['rule'], R3_TRASPASO_RULE)
        # 'TRANSF INTERNA' declara el patron especifico antes del generico ^TRANSF
        results = by_id(resolve_transfers(self.solo('TRANSF INTERNA/ACME')))
        self.assertEqual(results['solo']['resolved_class'], 'internal_transfer')
        # ninguna fila puede ser tocada por dos patrones: con un mix de
        # descripciones, cada fila lleva exactamente un patron asignado
        rows = []
        for index, desc in enumerate(('TRASPASO A CTA 1', 'TRANSFERENCIA A PROVEEDOR',
                                      'TRANSFER COMMISSION', 'STRIPE PAYOUT',
                                      'DISP.ENTREG.EFECT.', 'RETORNO TRASPASO 55')):
            rows.append(base_row(transaction_id=f'r{index}', date_ok=date(2025, 1, 1) + timedelta(days=index * 30),
                                 description=desc,
                                 amount=10.0 * (1 if index % 2 else -1),
                                 amount_eur=10.0 * (1 if index % 2 else -1)))
        results = resolve_transfers(rows)
        for row in results:
            tocados = [p for p, _c, _k, _j in DESC_PATTERNS
                       if row['pattern'] is not None and row['pattern'] == p]
            self.assertLessEqual(len(tocados), 1)
        self.assertEqual(len({row['pattern'] for row in results if row['pattern']}), 6)

    def test_r3_es_determinista_con_descripciones(self):
        rows = self.solo('Traspaso A CTA 123') + [base_row(
            transaction_id='otro', description='Transferencia a Proveedor',
            amount=-40.0, amount_eur=-40.0)]
        self.assertEqual(resolve_transfers(rows), resolve_transfers(rows))

    @unittest.skipUnless(paths.CLEAN_DIR.exists() and paths.MARTS_DIR.exists(),
                         'Workspace publicado no disponible')
    def test_r3_cli_no_cambia_sha256_de_fuentes(self):
        watched = [directory for directory in (paths.CLEAN_DIR, paths.INTERIM_DIR, paths.MARTS_DIR)
                   if directory.exists()]

        def digests():
            result = {}
            for directory in watched:
                for file in sorted(directory.glob('*.parquet')):
                    with file.open('rb') as stream:
                        result[str(file)] = hashlib.file_digest(stream, 'sha256').hexdigest()
            return result

        before = digests()
        with tempfile.TemporaryDirectory() as directory:
            run_main(['--output', str(Path(directory) / 'transfer_resolution_v2')])
        self.assertEqual(digests(), before)


if __name__ == '__main__':
    unittest.main()
