"""JSON Schema for the model boundary; no HTTP or framework dependency."""
from scripts.financial_v3_contract import INPUT_VERSION, input_schemas

from .contract import MAX_RECORDS, SCHEMA_VERSION, SUPPLEMENTS
from .features import FEATURE_NAMES

SOURCE = {
    'source_id': 'id', 'source_sha256': 'hash', 'record_id': 'id', 'effective_at': 'timestamp',
    'known_on': 'timestamp?', 'retrieved_at': 'timestamp', 'source_timezone': 'id',
    'availability_basis': 'enum:verified|retrospective|assumed|unknown',
}


def obj(properties, *, optional=()):
    return {'type': 'object', 'properties': properties,
            'required': [k for k in properties if k not in optional], 'additionalProperties': False}


def field(spec):
    if spec.endswith('?'):
        return {'anyOf': [field(spec[:-1]), {'type': 'null'}]}
    if spec.startswith('enum:'):
        return {'type': 'string', 'enum': spec[5:].split('|')}
    if spec in ('ids', 'decimals'):
        return {'type': 'array', 'items': field('id' if spec == 'ids' else 'decimal'),
                **({'uniqueItems': True} if spec == 'ids' else {})}
    if spec == 'source':
        return obj({k: field(v) for k, v in SOURCE.items()})
    return {
        'input_version': {'const': INPUT_VERSION},
        'bool': {'type': 'boolean'},
        'id': {'type': 'string', 'minLength': 1, 'maxLength': 512, 'pattern': r'^\S(?:[^\x00-\x1f]*\S)?$'},
        'hash': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
        'currency': {'type': 'string', 'pattern': '^[A-Z]{3}$'},
        'date': {'type': 'string', 'format': 'date', 'pattern': r'^\d{4}-\d{2}-\d{2}$'},
        'timestamp': {'type': 'string', 'format': 'date-time',
                      'pattern': r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$'},
        'decimal': {'type': 'string', 'pattern': r'^-?(0|[1-9][0-9]*)(\.[0-9]{1,12})?$', 'maxLength': 40},
    }[spec]


def input_schema():
    def collection(spec):
        return {'type': 'array', 'items': obj({k: field(v) for k, v in spec.items()}), 'maxItems': MAX_RECORDS}
    records = {kind: collection(spec) for kind, spec in input_schemas().items()}
    result = obj({
        'schema_version': {'const': SCHEMA_VERSION},
        **{k: field('id') for k in ('request_id', 'company_id', 'group_id', 'snapshot_id')},
        'as_of': {**field('date'), 'description': 'Closed calendar month-end in UTC.'},
        'view': {'type': 'string', 'enum': ['as_of', 'reconstructed_retrospective'], 'default': 'as_of'},
        'reporting_currency': field('currency'),
        'records': obj(records, optional=tuple(records)),
        **{name: collection(spec) for name, spec in SUPPLEMENTS.items()},
    }, optional=('view',))
    result.update({'$schema': 'https://json-schema.org/draft/2020-12/schema',
        '$id': 'urn:predictive-model:assessment-input:1', 'title': 'Modelo predictivo — snapshot financiero',
        '$comment': 'Runtime additionally checks references, temporal visibility, exact decimals (38 total digits), aggregate size, and cross-field invariants. Empty records are unknown, not zero.'})
    return result


def output_schema():
    nullable_number = {'type': ['number', 'null']}
    strings = {'type': 'array', 'items': {'type': 'string'}}
    prediction = obj({'target': {'type': 'string'},
        'probabilities': {'anyOf': [{'type': 'null'}, {'type': 'object', 'properties': {
            '0': {'type': 'number', 'minimum': 0, 'maximum': 1}, '1': {'type': 'number', 'minimum': 0, 'maximum': 1}},
            'required': ['0', '1'], 'additionalProperties': False}]},
        'reasons': strings, 'horizon_start': field('date'), 'horizon_end': field('date'),
        'calibration': {'const': 'uncalibrated_internal_retrospective'},
        'scope': {'const': 'classified_operating_receipts_not_solvency'}, 'fit_label_cutoff': field('date?')})
    targets = ('receipt_contraction_3m', 'receipt_expansion_3m', 'current_receipt_dip_3m')
    result = obj({
        'schema_version': {'const': SCHEMA_VERSION},
        **{k: field('id') for k in ('request_id', 'company_id', 'group_id', 'snapshot_id',
                                  'model_version', 'feature_version', 'policy_version')},
        'as_of': field('date'), 'view': {'enum': ['as_of', 'reconstructed_retrospective']},
        'input_sha256': field('hash'), 'bundle_sha256': field('hash'), 'evaluated_at': field('timestamp'),
        'financial': {'type': 'object', 'required': ['core_version', 'company_id', 'as_of', 'view', 'score', 'features',
            'cash', 'obligations', 'limitations'], 'description': 'Versioned financial core result; money values are decimal strings. Serving nulls paid aggregates that would mix native currencies.'},
        'change': {'type': 'object', 'required': ['value', 'reasons', 'direction', 'causal_claim'],
                   'properties': {'value': nullable_number, 'reasons': strings, 'causal_claim': {'const': False}}},
        'predictions': obj({target: {**prediction, 'properties': {
            **prediction['properties'], 'target': {'const': target},
            'probabilities': {'anyOf': [{'type': 'null'}, obj({
                label: {'type': 'number', 'minimum': 0, 'maximum': 1}
                for label in (('recovered', 'persistent', 'mixed') if target == 'current_receipt_dip_3m' else ('0', '1'))
            })]},
        }} for target in targets}),
        'model_inputs': obj({'values': obj(dict.fromkeys(FEATURE_NAMES, nullable_number)),
            'eligible': {'type': 'boolean'}, 'reasons': strings, 'missing_features': strings,
            'currency_basis': {'const': 'EUR'}, 'scope': {'const': 'observed_cash_perimeter'},
            'feature_order': {'const': list(FEATURE_NAMES)}}),
        'explanations': obj({'score': {'type': 'object'}, 'prediction': {'type': 'string'}}),
        'warnings': strings,
    })
    result.update({'$schema': 'https://json-schema.org/draft/2020-12/schema',
                   '$id': 'urn:predictive-model:assessment-output:1', 'title': 'Modelo predictivo — evaluación'})
    return result
