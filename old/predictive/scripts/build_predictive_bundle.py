"""Build/check a portable predictive release without training or altering sealed inputs."""

# ruff: noqa: E402, RUF100 -- late imports prevent --check from writing bytecode.

import argparse
import sys
from pathlib import Path

# --check must not create import caches, even when invoked without python -B.
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.financial_v3_contract import (
    POLICY_VERSION,
    canonical_bytes,
    default_policy,
    parse_json,
    require,
)
from services.predictive.predictive_model.bundle import (
    DATA_FILES,
    FEATURE_VERSION,
    FEATURES,
    MODEL_VERSION,
    PYTHON_VERSION,
    SCHEMA_VERSION,
    SOURCE_DIRECTORY,
    SOURCE_MANIFEST_SHA256,
    Bundle,
    no_symlinks,
    read_bounded,
    sha256,
)

HISTORICAL_MODULES = tuple(f'scripts/financial_v3_{name}.py' for name in (
    'contract', 'cash', 'obligations', 'features', 'score', 'engine',
))
PACKAGE_DIRECTORY = 'services/predictive/predictive_model'
RELEASE_MANIFEST = 'release-manifest.json'
CACHE_DIRECTORIES = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'}


def json_bytes(value):
    return canonical_bytes(value) + b'\n'


def release_payloads(root=ROOT):
    """Read and pin sources first; return deterministic relative-path payloads."""
    root = no_symlinks(root)
    sources = {}

    def source(relative):
        payload = read_bounded(root / relative)
        sources[relative] = sha256(payload)
        return payload

    original = source(f'{SOURCE_DIRECTORY}/manifest.json')
    require(sha256(original) == SOURCE_MANIFEST_SHA256, 'Original model manifest SHA256 mismatch')
    original_manifest = parse_json(original)
    original_data = {}
    for name in ('models.json', 'index.json', 'seal.json'):
        payload = source(f'{SOURCE_DIRECTORY}/{name}')
        require(sha256(payload) == original_manifest['artifacts_sha256'][name],
                f'Original {name} SHA256 mismatch')
        original_data[name] = payload
    index = parse_json(original_data['index.json'])
    sealed_sources = parse_json(original_data['seal.json'])['bindings_sha256']
    files = {
        'bundle/models.json': original_data['models.json'],
        'bundle/policy.json': json_bytes(default_policy()),
        'bundle/features.json': json_bytes({'feature_version': FEATURE_VERSION, 'names': list(FEATURES)}),
        'bundle/exclusions.json': json_bytes({'excluded_product_only': index['excluded_product_only']}),
        'scripts/__init__.py': b'',
    }
    for relative in HISTORICAL_MODULES:
        files[relative] = source(relative)
        require(sha256(files[relative]) == sealed_sources[relative],
                f'Historical source differs from sealed scientific version: {relative}')
    package = no_symlinks(root / PACKAGE_DIRECTORY)
    require(package.is_dir(), 'Predictive package source is missing')
    for path in sorted(package.rglob('*')):
        relative = path.relative_to(package)
        if set(relative.parts) & CACHE_DIRECTORIES or path.suffix in ('.pyc', '.pyo'):
            continue
        no_symlinks(path)
        if path.is_dir():
            continue
        files[f'predictive_model/{relative.as_posix()}'] = source(path.relative_to(root).as_posix())
    require('predictive_model/bundle.py' in files, 'Bundle loader source is missing')
    for name in ('request.schema.json', 'response.schema.json', 'example.json'):
        relative = f'contracts/predictive/{name}'
        files[relative] = source(relative)
    for name in ('README.md', '.python-version'):
        files[name] = source(f'services/predictive/{name}')
    source('scripts/build_predictive_bundle.py')
    manifest = {
        'format_version': 1,
        'model_version': MODEL_VERSION,
        'feature_version': FEATURE_VERSION,
        'policy_version': POLICY_VERSION,
        'schema_version': SCHEMA_VERSION,
        'python_version': PYTHON_VERSION,
        'files': {name: sha256(files[f'bundle/{name}']) for name in DATA_FILES},
        'provenance': {
            'source_directory': SOURCE_DIRECTORY,
            'manifest_sha256': SOURCE_MANIFEST_SHA256,
            'seal_sha256': sha256(original_data['seal.json']),
            'models_sha256': sha256(original_data['models.json']),
            'index_sha256': sha256(original_data['index.json']),
        },
    }
    files['bundle/manifest.json'] = json_bytes(manifest)
    bundle_sha256 = sha256(files['bundle/manifest.json'])
    files[RELEASE_MANIFEST] = json_bytes({
        'format_version': 1,
        'python_version': PYTHON_VERSION,
        'bundle_sha256': bundle_sha256,
        'files': {name: sha256(payload) for name, payload in sorted(files.items())},
        'sources': dict(sorted(sources.items())),
    })
    return files, bundle_sha256


def check_release(output, files, bundle_sha256):
    """Compare exact inventories and bytes; never execute code from the release."""
    output = no_symlinks(output)
    require(output.is_dir(), 'Release directory does not exist')
    expected_directories = {str(parent) for name in files
                            for parent in Path(name).parents if str(parent) != '.'}
    actual_files, actual_directories = set(), set()
    for path in output.rglob('*'):
        no_symlinks(path)
        relative = path.relative_to(output).as_posix()
        if path.is_dir():
            actual_directories.add(relative)
        else:
            actual_files.add(relative)
    require(actual_files == set(files) and actual_directories == expected_directories,
            'Unexpected or missing release files/directories')
    for name, expected in files.items():
        require(read_bounded(output / name) == expected, f'Release or source changed: {name}')
    return Bundle.load(output / 'bundle', expected_sha256=bundle_sha256)


def build(output, *, check=False, root=ROOT):
    output = no_symlinks(output)
    files, bundle_sha256 = release_payloads(root)
    if check or output.exists():
        check_release(output, files, bundle_sha256)
    else:
        # Exclusive creation: never merge with or overwrite another release.
        output.mkdir(parents=True, exist_ok=False)
        for name, payload in files.items():
            destination = output / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('xb') as stream:
                stream.write(payload)
        check_release(output, files, bundle_sha256)
    return {'output_dir': str(output), 'bundle_sha256': bundle_sha256,
            'release_manifest_sha256': sha256(files[RELEASE_MANIFEST]),
            'model_version': MODEL_VERSION, 'feature_version': FEATURE_VERSION,
            'python_version': PYTHON_VERSION, 'checked': check}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.local/predictive-model')
    parser.add_argument('--check', action='store_true', help='Verify release and source hashes without writes')
    args = parser.parse_args(argv)
    try:
        result = build(args.output, check=args.check)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f'Predictive bundle failed: {error}\n')
    print(canonical_bytes(result).decode('utf-8'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
