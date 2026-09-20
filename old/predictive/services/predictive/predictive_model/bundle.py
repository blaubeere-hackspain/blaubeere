"""Pinned JSON bundle loading and the selected persistence estimators; stdlib only."""

import hashlib
import math
import re
from pathlib import Path
from typing import Any

from scripts.financial_v3_contract import (
    POLICY_VERSION,
    exact_keys,
    finite_number,
    identifier,
    iso_date,
    parse_json,
    require,
    validate_policy,
)

MODEL_VERSION = 'predictive-model-1'
FEATURE_VERSION = 'predictive-features-1'
SCHEMA_VERSION = 'predictive-assessment-1'
PYTHON_VERSION = '3.13.15'
FEATURES = tuple(k for name in (
    'receipts_known', 'payments_known', 'operating_low', 'operating_high',
    'classification_coverage', 'eur_coverage',
) for k in (name, name + '_mean3')) + (
    'ap_due_face', 'ap_issued_known', 'ar_issued_known', 'unknown_due_rows',
    'due_missing_eur_rows', 'service_paid_known',
)
TARGETS = ('receipt_contraction_3m', 'receipt_expansion_3m', 'current_receipt_dip_3m')
SOURCE_DIRECTORY = 'reports/modeling/financial-v3/dataset-v1/model-v1'
SOURCE_MANIFEST_SHA256 = '71eae8fef2ab3466fa2545d1c38f57977508cb79b7c051280b59e02253925428'
DATA_FILES = ('models.json', 'policy.json', 'features.json', 'exclusions.json')
MAX_FILE_BYTES = 1024 * 1024


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def validate_sha256(value):
    require(type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) is not None,
            'Expected lowercase SHA256 hex digest')
    return value


def no_symlinks(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'Symlinks are not allowed')
    return path


def read_bounded(path, maximum=MAX_FILE_BYTES):
    path = no_symlinks(path)
    require(path.is_file(), f'Expected regular file: {path.name}')
    with path.open('rb') as stream:
        payload = stream.read(maximum + 1)
    require(len(payload) <= maximum, f'File exceeds size limit: {path.name}')
    return payload


def _probabilities(value):
    exact_keys(value, ('0', '1'))
    require(all(0 <= finite_number(p) <= 1 for p in value.values()), 'Probability outside [0, 1]')
    require(math.isclose(sum(value.values()), 1, rel_tol=0, abs_tol=1e-12),
            'Probabilities must sum to one')


def _validate_model(target, model: dict[str, Any] | None):
    if model is None:
        return
    require(type(model) is dict and model.get('candidate') == 'persistence',
            'Unsupported selected candidate; only persistence is implemented')
    require(target != TARGETS[2], 'Current dip estimator must remain unavailable')
    exact_keys(model, (
        'candidate', 'target', 'classes', 'prevalence', 'training_support', 'feature_order',
        'real_input_model', 'calibration', 'seed', 'states', 'fit_label_cutoff',
        'fit_origin_windows', 'fitted_at', 'historical_issuance', 'selected_at', 'selection_cutoff',
    ))
    require(model['target'] == target, 'Model target mismatch')
    require(type(model['classes']) is list and model['classes'] == [0, 1]
            and all(type(c) is int for c in model['classes']), 'Incompatible model classes')
    require(model['feature_order'] == list(FEATURES), 'Incompatible feature order')
    require(type(model['real_input_model']) is bool and model['real_input_model']
            and type(model['historical_issuance']) is bool and not model['historical_issuance'],
            'Incompatible model provenance')
    require(model['calibration'] == 'uncalibrated_internal_retrospective', 'Incompatible calibration')
    require(type(model['seed']) is int and model['seed'] == 1729, 'Incompatible model seed')
    for key in ('fit_label_cutoff', 'selection_cutoff'):
        iso_date(model[key])
    require(type(model['fit_origin_windows']) is list, 'Invalid fit windows')
    for window in model['fit_origin_windows']:
        require(type(window) is list and len(window) == 2, 'Invalid fit window')
        require(iso_date(window[0]) <= iso_date(window[1]), 'Invalid fit window order')
    for key in ('fitted_at', 'selected_at'):
        identifier(model[key])
    support = model['training_support']
    exact_keys(support, ('adequate', 'companies', 'groups', 'minimum_groups_per_class',
                         'minimum_rows_per_class', 'minimum_total_groups', 'per_class', 'rows'))
    require(type(support['adequate']) is bool and support['adequate'], 'Selected model lacks adequate support')
    for key in set(support) - {'adequate', 'per_class'}:
        require(type(support[key]) is int and support[key] > 0, 'Invalid training support')
    exact_keys(support['per_class'], ('0', '1'))
    for counts in support['per_class'].values():
        exact_keys(counts, ('companies', 'groups', 'rows'))
        require(all(type(n) is int and n > 0 for n in counts.values()), 'Invalid class support')
    _probabilities(model['prevalence'])
    require(type(model['states']) is dict and set(model['states']) <= {'low', 'middle', 'high', 'unknown'},
            'Invalid persistence states')
    for probabilities in model['states'].values():
        _probabilities(probabilities)


class Bundle:
    """Load only trusted manifest bytes, then validate every bounded data file."""

    sha256: str
    models: dict[str, Any]
    policy: dict[str, Any]
    feature_names: tuple[str, ...]
    excluded_company_ids: set[str]
    excluded_group_ids: set[str]
    model_version: str
    feature_version: str
    metadata: dict[str, Any]

    @classmethod
    def load(cls, path, *, expected_sha256: str) -> 'Bundle':
        validate_sha256(expected_sha256)
        path = no_symlinks(path)
        require(path.is_dir(), 'Expected bundle directory')
        if (path / 'bundle').is_dir():
            path = no_symlinks(path / 'bundle')
        require({p.name for p in path.iterdir()} == {*DATA_FILES, 'manifest.json'},
                'Unexpected or missing bundle files')
        payload = read_bounded(path / 'manifest.json')
        require(sha256(payload) == expected_sha256, 'Bundle manifest SHA256 mismatch')
        metadata: Any = parse_json(payload)
        exact_keys(metadata, ('format_version', 'model_version', 'feature_version', 'policy_version',
                              'schema_version', 'python_version', 'files', 'provenance'))
        require(type(metadata['format_version']) is int and metadata['format_version'] == 1,
                'Incompatible bundle format')
        for key, expected in (
            ('model_version', MODEL_VERSION), ('feature_version', FEATURE_VERSION),
            ('policy_version', POLICY_VERSION), ('schema_version', SCHEMA_VERSION),
            ('python_version', PYTHON_VERSION),
        ):
            require(metadata[key] == expected, f'Incompatible {key}')
        provenance = metadata['provenance']
        exact_keys(provenance, ('source_directory', 'manifest_sha256', 'seal_sha256', 'models_sha256', 'index_sha256'))
        require(provenance['source_directory'] == SOURCE_DIRECTORY
                and provenance['manifest_sha256'] == SOURCE_MANIFEST_SHA256, 'Incompatible source provenance')
        for key in ('manifest_sha256', 'seal_sha256', 'models_sha256', 'index_sha256'):
            validate_sha256(provenance[key])
        exact_keys(metadata['files'], DATA_FILES)
        documents: dict[str, Any] = {}
        for name in DATA_FILES:
            expected = validate_sha256(metadata['files'][name])
            payload = read_bounded(path / name)
            require(sha256(payload) == expected, f'Bundle file SHA256 mismatch: {name}')
            documents[name] = parse_json(payload)
        features = documents['features.json']
        exact_keys(features, ('feature_version', 'names'))
        require(features['feature_version'] == FEATURE_VERSION and features['names'] == list(FEATURES),
                'Incompatible feature contract')
        policy = validate_policy(documents['policy.json'])
        models = documents['models.json']
        exact_keys(models, TARGETS)
        for target, model in models.items():
            _validate_model(target, model)
        exclusions = documents['exclusions.json']
        exact_keys(exclusions, ('excluded_product_only',))
        require(type(exclusions['excluded_product_only']) is list, 'Invalid exclusions')
        companies, groups = set(), set()
        for row in exclusions['excluded_product_only']:
            exact_keys(row, ('company_id', 'group_id', 'reason'))
            identifier(row['company_id'])
            identifier(row['group_id'])
            require(row['reason'] == 'final_test_product_only_no_model_inference', 'Invalid exclusion reason')
            require(row['company_id'] not in companies, 'Duplicate excluded company')
            companies.add(row['company_id'])
            groups.add(row['group_id'])
        result = cls()
        result.sha256 = expected_sha256
        result.models = models
        result.policy = policy
        result.feature_names = FEATURES
        result.excluded_company_ids = companies
        result.excluded_group_ids = groups
        result.model_version = MODEL_VERSION
        result.feature_version = FEATURE_VERSION
        result.metadata = metadata
        return result

    def predict(self, target, values: dict) -> dict[str, float] | None:
        require(type(target) is str and target in TARGETS, 'Unknown prediction target')
        model = self.models[target]
        if model is None:
            return None
        exact_keys(values, FEATURES)
        for value in values.values():
            if value is not None:
                finite_number(value)
        current, mean = values['receipts_known'], values['receipts_known_mean3']
        state = 'unknown'
        if current is not None and mean is not None and mean > 0:
            state = 'low' if current <= .8 * mean else 'high' if current >= 1.2 * mean else 'middle'
        return dict(model['states'].get(state, model['prevalence']))
