import copy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from scripts import jev_pilot as pilot


class PayloadTests(unittest.TestCase):
    def test_valid_payload_and_http_contract(self):
        payload = pilot.build_payload(['operating_in', 'unknown'], ['INVENTED receipt'], 'Only evidence')
        self.assertEqual(payload['tier'], 'fast')
        self.assertEqual(pilot.HTTP_CONTRACT['method'], 'POST')
        self.assertEqual(pilot.HTTP_CONTRACT['url'], 'https://classifier.dev')
        self.assertEqual(pilot.HTTP_CONTRACT['headers']['Content-Type'], 'application/json')
        self.assertTrue(pilot.HTTP_CONTRACT['headers']['User-Agent'])
        pilot.validate_payload(payload)

    def test_limits_and_smart_rejected(self):
        for labels, inputs, instructions, tier in [
            (['unknown'], ['a'], 'x', 'fast'),
            (['unknown'] + [str(i) for i in range(100)], ['a'], 'x', 'fast'),
            (['a', 'a', 'unknown'], ['a'], 'x', 'fast'),
            (['a', 'b'], ['a'], 'x', 'fast'),
            (['a', 'unknown'], [], 'x', 'fast'),
            (['a', 'unknown'], ['a'] * 1001, 'x', 'fast'),
            (['a', 'unknown'], ['a' * 32001], 'x', 'fast'),
            (['a', 'unknown'], [None], 'x', 'fast'),
            (['a', 'unknown'], ['a'], None, 'fast'),
            (['a', 'unknown'], ['a'], 'x', 'smart'),
        ]:
            with self.subTest(labels=len(labels), tier=tier, inputs=len(inputs)):
                with self.assertRaises(ValueError):
                    pilot.build_payload(labels, inputs, instructions, tier)
        payload = pilot.build_payload(['a', 'unknown'], ['a' * 32000] * 1000, 'x')
        pilot.validate_payload(payload)
        payload['multi'] = True
        with self.assertRaises(ValueError):
            pilot.validate_payload(payload)


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.use = 'movimientos'
        self.protocol = pilot.protocols()[self.use]
        self.payload = pilot.build_payload(self.protocol['taxonomy'], ['INVENTED incoming sale'], self.protocol['instructions'])
        self.refs = ['invented:001']
        self.contexts = [{'evidence_sufficient': True, 'direction': 'in'}]
        self.auth = pilot.MockAuthorization(
            local_mock_authorized=True, max_calls=1, max_inputs=1,
            allowed_models=('invented-model-v1',), allowed_routers=('classifier.dev',),
            thresholds=tuple((label, 0.8) for label in self.payload['labels']),
        )
        self.response = self.response_for('operating_in')

    def response_for(self, label, confidence=0.99):
        return {'results': [{'label': label, 'confidence': confidence,
                            'scores': {item: float(item == label) for item in self.payload['labels']},
                            'model': 'invented-model-v1'}],
                'modelsUsed': ['invented-model-v1'], 'model': 'invented-model-v1'}

    def parse(self, response=None, auth=None):
        return pilot.parse_response(self.payload, self.response if response is None else response,
                                    self.refs, self.contexts, self.protocol, auth or self.auth)

    def test_proposal_only_and_minimized_snapshot(self):
        snapshot = self.parse()[0]
        self.assertEqual(snapshot['decision'], 'proposal_for_review')
        self.assertEqual(snapshot['proposal'], 'operating_in')
        self.assertFalse(snapshot['adopted'])
        self.assertTrue(snapshot['retain_case'])
        self.assertNotIn('INVENTED incoming sale', pilot.canonical(snapshot))
        self.assertEqual(len(snapshot['input_sha256']), 64)
        self.assertNotIn('promoted', snapshot)

    def test_positional_order_and_local_ref_cardinality(self):
        self.payload['inputs'] *= 2
        self.refs = ['invented:b', 'invented:a']
        self.contexts *= 2
        self.response['results'] *= 2
        snapshots = self.parse()
        self.assertEqual([s['input_ref'] for s in snapshots], self.refs)
        self.assertEqual([s['input_index'] for s in snapshots], [0, 1])
        self.response['results'].pop()
        self.assertTrue(all(s['decision'] == 'abstain' for s in self.parse()))
        self.refs = ['invented:b', 'invented:b']
        with self.assertRaises(ValueError):
            self.parse()

    def test_bad_confidence_always_abstains(self):
        for value in (None, True, False, float('nan'), float('inf'), -0.1, 1.1, '0.99', 10 ** 1000):
            with self.subTest(value=value):
                result = self.parse(self.response_for('operating_in', value))[0]
                self.assertEqual(result['decision'], 'abstain')
                self.assertIsNone(result['proposal'])
                pilot.canonical(result)

    def test_unknown_insufficient_unscored_and_low_confidence(self):
        for label in ('unknown', 'evidencia_insuficiente'):
            self.assertEqual(self.parse(self.response_for(label))[0]['decision'], 'abstain')
        self.assertEqual(self.parse(self.response_for('operating_in', 0.1))[0]['reason'], 'low_confidence')
        self.response['results'][0]['unscored'] = 'sensitive diagnostic must not persist'
        result = self.parse()[0]
        self.assertEqual(result['decision'], 'abstain')
        self.assertNotIn('sensitive diagnostic', pilot.canonical(result))

    def test_malformed_scores_model_roster_and_smart_abstain(self):
        variants = [None, {}, {'results': []}, {'results': 'not a list'}]
        for key, value in [('label', ['operating_in']), ('scores', None),
                           ('scores', {'wrong': 1.0}), ('model', 'unapproved'),
                           ('escalated', True), ('confidence', {'value': 0.99})]:
            response = copy.deepcopy(self.response)
            response['results'][0][key] = value
            variants.append(response)
        for value in (True, float('nan'), float('inf'), -1, 2, '0.5'):
            response = copy.deepcopy(self.response)
            response['results'][0]['scores']['unknown'] = value
            variants.append(response)
        for roster in ([], ['invented-model-v1', 'other'], ['other'], 'invented-model-v1'):
            response = copy.deepcopy(self.response)
            response['modelsUsed'] = roster
            variants.append(response)
        response = copy.deepcopy(self.response)
        response['model'] = 'mixed'
        variants.append(response)
        response = copy.deepcopy(self.response)
        response['tier'] = 'smart'
        variants.append(response)
        for response in variants:
            with self.subTest(response=response):
                result = pilot.parse_response(self.payload, response, self.refs, self.contexts, self.protocol, self.auth)
                self.assertEqual(result[0]['decision'], 'abstain')

    def test_mixed_roster_with_unapproved_model_abstains_entire_batch(self):
        self.payload['inputs'] *= 2
        self.refs = ['invented:a', 'invented:b']
        self.contexts *= 2
        self.response['results'].append(dict(self.response['results'][0], model='unapproved'))
        self.response['modelsUsed'].append('unapproved')
        self.response['model'] = 'mixed'
        self.assertTrue(all(item['decision'] == 'abstain' for item in self.parse()))

    def test_model_and_threshold_configuration_required(self):
        for auth in (replace(self.auth, allowed_models=()), replace(self.auth, thresholds=()),
                     replace(self.auth, thresholds=(('operating_in', None),))):
            self.assertEqual(self.parse(auth=auth)[0]['decision'], 'abstain')

    def test_direction_ambiguity_and_ownership_cannot_be_overridden(self):
        for context, label in [({'evidence_sufficient': True, 'direction': 'out'}, 'operating_in'),
                               ({'evidence_sufficient': True, 'direction': 'in', 'ambiguous': True}, 'operating_in'),
                               ({'evidence_sufficient': True, 'direction': 'in'}, 'transfer'),
                               ({'evidence_sufficient': True, 'economic_conflict': True}, 'operating_in'),
                               ({}, 'operating_in')]:
            self.contexts = [context]
            self.assertEqual(self.parse(self.response_for(label))[0]['decision'], 'abstain')

    def test_use_specific_economic_guards(self):
        cases = [('documentos', 'invoice', {'evidence_sufficient': True, 'original_type': 'credit_note'}),
                 ('explicaciones', 'respaldada', {'evidence_sufficient': True, 'evidence_refs': [], 'as_of_valid': True}),
                 ('explicaciones', 'respaldada', {'evidence_sufficient': False}),
                 ('revision_humana', 'control_sin_alerta', {'evidence_sufficient': True, 'critical': True})]
        for use, label, context in cases:
            self.protocol = pilot.protocols()[use]
            self.payload = pilot.build_payload(self.protocol['taxonomy'], ['INVENTED example'], self.protocol['instructions'])
            self.auth = replace(self.auth, thresholds=tuple((item, 0.8) for item in self.payload['labels']))
            self.contexts = [context]
            result = self.parse(self.response_for(label))[0]
            self.assertEqual(result['decision'], 'abstain')
            self.assertTrue(result['retain_case'])

    def test_no_permission_budget_model_or_guarantee_prevents_callback(self):
        configurations = [pilot.MockAuthorization(), replace(self.auth, max_calls=0),
                          replace(self.auth, max_inputs=0), replace(self.auth, allowed_models=()),
                          replace(self.auth, thresholds=()), replace(self.auth, allowed_routers=()),
                          replace(self.auth, require_fixed_provider=True),
                          replace(self.auth, require_fixed_model=True), replace(self.auth, require_zdr=True)]
        for auth in configurations:
            callback = Mock(return_value=self.response)
            runner = pilot.MockRunner(auth)
            snapshots = runner.run(self.payload, self.refs, self.contexts, self.protocol, callback)
            callback.assert_not_called()
            self.assertEqual(runner.mock_calls, 0)
            self.assertEqual(runner.network_calls, 0)
            self.assertTrue(all(s['decision'] == 'abstain' for s in snapshots))

    def test_auth_immutable_and_real_input_always_disabled(self):
        with self.assertRaises(FrozenInstanceError):
            self.auth.local_mock_authorized = False
        with self.assertRaises((TypeError, ValueError)):
            pilot.MockAuthorization(allowed_models=['mutable'])
        callback = Mock(return_value=self.response)
        runner = pilot.MockRunner(self.auth)
        runner.run(self.payload, self.refs, self.contexts, self.protocol, callback, data_kind='local_dataset')
        callback.assert_not_called()
        runner.run(self.payload, self.refs, self.contexts, self.protocol, callback, route='gateway')
        callback.assert_not_called()

    def test_budget_consumed_no_retry_no_fallback(self):
        callback = Mock(side_effect=TimeoutError('secret diagnostic'))
        runner = pilot.MockRunner(self.auth)
        result = runner.run(self.payload, self.refs, self.contexts, self.protocol, callback)[0]
        self.assertEqual(result['reason'], 'service_failure_mock')
        self.assertTrue(result['retain_case'])
        self.assertNotIn('secret diagnostic', pilot.canonical(result))
        runner.run(self.payload, self.refs, self.contexts, self.protocol, callback)
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(runner.mock_calls, 1)
        self.assertEqual(runner.network_calls, 0)

    def test_default_has_no_callback(self):
        runner = pilot.MockRunner(self.auth)
        result = runner.run(self.payload, self.refs, self.contexts, self.protocol)[0]
        self.assertEqual(result['decision'], 'abstain')
        self.assertEqual(runner.mock_calls, 0)


class PreparationTests(unittest.TestCase):
    def test_protocols_are_independent_unapproved_and_unevaluated(self):
        protocols = pilot.protocols()
        self.assertEqual(len(protocols), 4)
        for use, protocol in protocols.items():
            self.assertEqual(protocol['use'], use)
            self.assertEqual(protocol['status'], 'prepared_not_evaluated')
            self.assertFalse(protocol['adopted'])
            self.assertEqual(set(protocol['authorizations'].values()), {False})
            self.assertEqual(set(protocol['budgets'].values()), {0})
            self.assertEqual(protocol['human_reference']['status'], 'absent')
            self.assertEqual(set(protocol['thresholds_by_class'].values()), {None})
            self.assertEqual(protocol['allowed_models'], [])
            self.assertEqual(protocol['allowed_routers'], [])
            self.assertFalse(protocol['gateway']['enabled'])
            self.assertTrue(all(value is None for value in protocol['acceptance_criteria'].values()))
            self.assertIn('unknown', protocol['taxonomy'])
            self.assertIn('evidencia_insuficiente', protocol['taxonomy'])

    def test_rule_queue_preserves_all_ids_critical_and_unknown_exposure(self):
        base = {'company_id': 'invented:a', 'group_id': 'invented:g', 'month': '2000-01-01',
                'available_at': '2000-02-01', 'n_conflictos_direccion': 1, 'n_sin_eur': 1,
                'n_servicio_sin_eur': 0, 'n_ambiguos': 1, 'n_atipicos_operativos': 0,
                'sensible_atipicos': False, 'volumen_caja_conocido_eur': 10,
                'estado_operativo': 'parcial', 'estado_servicio_deuda': 'publicable',
                'estado_cobro': 'insuficiente', 'motivos_operativo': ['clasificacion_incompleta'],
                'motivos_servicio_deuda': [], 'motivos_cobro': ['sin_erp_observado']}
        other = dict(base, company_id='invented:b', n_conflictos_direccion=0, n_sin_eur=0,
                     volumen_caja_conocido_eur=1000000)
        before = copy.deepcopy([base, other])
        queue = pilot.rule_queue([base, other])
        self.assertEqual([base, other], before)
        self.assertEqual(len(queue), 2)
        first = next(item for item in queue if item['company_id'] == 'invented:a')
        self.assertTrue(first['critical'])
        self.assertEqual(first['materiality']['exposure_lane'], 'missing_fx_unknown')
        self.assertIsNone(first['materiality']['unknown_exposure_eur'])
        self.assertEqual(first['materiality']['known_eur'], 10)
        self.assertEqual(first['review_status'], 'not_reviewed')
        self.assertEqual(first['source_refs'][0]['key']['company_id'], 'invented:a')
        self.assertEqual(first['motivos']['operativo'], base['motivos_operativo'])
        with self.assertRaises(ValueError):
            pilot.rule_queue([base, base])

    def test_dry_run_is_invented_and_never_opens_socket(self):
        with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            report = pilot.dry_run()
        self.assertEqual(report['network_calls'], 0)
        self.assertFalse(report['adopted'])
        self.assertIsNone(report['observed_cost'])
        self.assertIsNone(report['human_metrics'])
        self.assertTrue(report['mock'])
        self.assertTrue(all(s['input_ref'].startswith('invented:') for s in report['snapshots']))
        self.assertTrue(all(s['retain_case'] for s in report['snapshots']))

    def test_exclusive_writer_refuses_different_existing_content(self):
        path = Mock(spec=Path)
        path.exists.return_value = True
        path.read_text.return_value = 'different content'
        with self.assertRaises(FileExistsError):
            pilot.write_artifact(path, {'a': 1})
        path.open.assert_not_called()
        path.read_text.return_value = pilot.canonical({'a': 1}) + '\n'
        pilot.write_artifact(path, {'a': 1})
        path.open.assert_not_called()


if __name__ == '__main__':
    unittest.main()
