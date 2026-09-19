import copy
import unittest
from datetime import date

from scripts.model_features import (
    FEATURE_NAMES, build_features, feature_contract, last_day, shift_month,
)


def panel_rows(company='c1', group='g1', start=date(2024, 9, 1), months=24):
    rows = []
    for index in range(months):
        month = shift_month(start, index)
        rows.append({
            'company_id': company, 'group_id': group, 'month': month,
            'available_at': last_day(month), 'es_calentamiento': index < 3,
            'tiene_actividad_caja': True, 'n_sin_eur': 0, 'n_tx_caja': 10,
            'n_cuentas_activas': 2, 'volumen_caja_conocido_eur': 100.0,
            'neto_caja_eur': -20.0, 'operativo_min_eur': -25.0,
            'operativo_max_eur': -15.0, 'operativo_min_sin_atipicos_eur': -25.0,
            'operativo_max_sin_atipicos_eur': -15.0,
            'cobros_operativos_conocido_eur': 30.0, 'pagos_operativos_conocido_eur': 50.0,
            'financiacion_entradas_conocido_eur': 10.0,
            'financiacion_salidas_conocido_eur': 0.0,
            'ambiguo_entradas_conocido_eur': 5.0, 'ambiguo_salidas_conocido_eur': 5.0,
        })
    return rows


class ModelFeaturesTest(unittest.TestCase):
    def feature(self, rows, month=date(2025, 4, 1), cutoff=None):
        return build_features(rows, [('c1', month)], cutoff=cutoff)[0]

    def test_future_mutation_and_drop_do_not_change_past_features(self):
        rows = panel_rows()
        expected = self.feature(rows)
        mutated = copy.deepcopy(rows)
        for row in mutated:
            if row['month'] > date(2025, 4, 1):
                row.update(n_sin_eur=999, neto_caja_eur=1e15, es_calentamiento=True)
        self.assertEqual(expected, self.feature(mutated))
        self.assertEqual(expected, self.feature(rows[:8]))
        self.assertTrue(expected['eligible'])

    def test_calendar_gap_is_unknown_not_compressed_or_zero(self):
        rows = [r for r in panel_rows() if r['month'] != date(2025, 3, 1)]
        result = self.feature(rows)
        self.assertFalse(result['eligible'])
        self.assertIsNone(result['features']['net_ratio_mean_3m'])
        self.assertEqual(result['missing_reason']['net_ratio_mean_3m'], 'missing_month')

    def test_unknown_fx_and_zero_volume_never_become_ratios(self):
        for patch in ({'n_sin_eur': 1}, {'volumen_caja_conocido_eur': 0.0}):
            with self.subTest(patch=patch):
                rows = panel_rows()
                rows[7].update(patch)
                result = self.feature(rows)
                self.assertFalse(result['eligible'])
                self.assertIsNone(result['features']['net_ratio'])
                self.assertIsNone(result['features']['net_ratio_mean_6m'])

    def test_partial_month_and_late_availability_are_excluded(self):
        self.assertEqual(build_features(panel_rows(), [('c1', date(2025, 4, 1))],
                                        cutoff=date(2025, 4, 15)), [])
        rows = panel_rows()
        rows[7]['available_at'] = date(2025, 5, 1)
        self.assertEqual(build_features(rows, [('c1', date(2025, 4, 1))]), [])
        rows[6]['available_at'] = date(2025, 5, 1)
        rows[7]['available_at'] = date(2025, 4, 30)
        self.assertFalse(self.feature(rows)['eligible'])

    def test_target_columns_and_ids_are_not_predictors(self):
        rows = panel_rows()
        expected = self.feature(rows)
        for row in rows:
            row.update(target_deficit_3m=True, objetivo_observable=False,
                       ultimo_mes_con_tx=date(2099, 1, 1), n_meses_futuros_con_caja=0)
        result = self.feature(rows)
        self.assertEqual(expected, result)
        self.assertEqual(tuple(result['features']), FEATURE_NAMES)
        self.assertEqual(len(FEATURE_NAMES), 32)
        self.assertFalse({'company_id', 'group_id', 'month'} & set(FEATURE_NAMES))
        self.assertEqual(feature_contract()['feature_order'], list(FEATURE_NAMES))

    def test_six_month_incomplete_is_unknown_but_three_month_eligible(self):
        result = self.feature(panel_rows(months=4), month=date(2024, 12, 1))
        self.assertTrue(result['eligible'])
        self.assertAlmostEqual(result['features']['net_ratio_mean_3m'], -0.2)
        self.assertIsNone(result['features']['net_ratio_mean_6m'])
        self.assertEqual(result['coverage']['valid_months_6m'], 4)

    def test_warmup_and_history_are_required_without_future_labels(self):
        rows = panel_rows(months=3)
        self.assertFalse(self.feature(rows, month=date(2024, 11, 1))['eligible'])
        rows[-1]['es_calentamiento'] = False
        self.assertFalse(self.feature(rows, month=date(2024, 11, 1))['eligible'])
        result = self.feature(panel_rows(), month=date(2026, 8, 1))
        self.assertTrue(result['eligible'])
        self.assertNotIn('target_deficit_3m', result)

    def test_cents_and_trimmed_robustness_match_deficit_state(self):
        rows = panel_rows()
        for row in rows:
            row.update(operativo_min_eur=-0.004, operativo_max_eur=-0.004,
                       operativo_min_sin_atipicos_eur=-0.004,
                       operativo_max_sin_atipicos_eur=-0.004)
        self.assertEqual(self.feature(rows)['features']['deficit_frequency_3m'], 0.0)
        for row in rows:
            row.update(operativo_min_eur=-0.005, operativo_max_eur=-0.005,
                       operativo_min_sin_atipicos_eur=-0.005,
                       operativo_max_sin_atipicos_eur=-0.005)
        self.assertEqual(self.feature(rows)['features']['deficit_frequency_3m'], 1.0)
        rows[7]['operativo_max_sin_atipicos_eur'] = 1.0
        self.assertIsNone(self.feature(rows)['features']['deficit_frequency_3m'])

    def test_past_window_deltas_and_population_statistics(self):
        rows = panel_rows()
        rows[7]['neto_caja_eur'] = 40.0
        result = self.feature(rows)['features']
        self.assertAlmostEqual(result['net_ratio_mean_3m'], 0.0)
        self.assertAlmostEqual(result['net_ratio_std_3m'], 0.282842712474619)
        self.assertAlmostEqual(result['net_ratio_slope_3m'], 0.3)
        self.assertAlmostEqual(result['net_ratio_delta_prior_3m'], 0.6)

    def test_duplicate_keys_and_changing_groups_fail_closed(self):
        rows = panel_rows()
        with self.assertRaises(ValueError):
            self.feature(rows + [rows[0]])
        rows[1]['group_id'] = 'another'
        with self.assertRaises(ValueError):
            self.feature(rows)


if __name__ == '__main__':
    unittest.main()
