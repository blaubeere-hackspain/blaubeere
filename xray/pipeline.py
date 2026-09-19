import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb

from xray import paths
from xray.schema import TABLES
from xray.period import FIRST_MONTH, DATASET_END


def digest(file):
    with Path(file).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with Path(source).open('rb') as incoming:
                shutil.copyfileobj(incoming, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def publish_run(root, run, manifest):
    root, run = Path(root), Path(run)
    if not run.resolve().is_relative_to((root / 'data/runs').resolve()):
        raise ValueError('La publicacion debe proceder de data/runs')
    outputs = manifest['outputs']
    for relative, expected in outputs.items():
        if not (run / relative).resolve().is_relative_to(run.resolve()):
            raise ValueError('Ruta de salida fuera de la ejecucion')
        if digest(run / relative) != expected:
            raise ValueError(f'El artefacto cambio tras validarse: {relative}')
    reports = root / 'reports'
    reports.mkdir(parents=True, exist_ok=True)
    lock = reports / '.publish.lock'
    with lock.open('x') as stream:
        stream.write(run.name)
    previous = {}
    published = []
    try:
        for relative in [*outputs, 'reports/build_manifest.json', 'reports/current.json']:
            target = root / relative
            previous[relative] = target.exists()
            if target.exists():
                backup = run / 'before' / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
        for relative in outputs:
            atomic_copy(run / relative, root / relative)
            published.append(relative)
        atomic_copy(run / 'manifest.json', reports / 'build_manifest.json')
        published.append('reports/build_manifest.json')
        pointer = run / 'current.json'
        pointer.write_text(json.dumps({
            'workspace': run.relative_to(root).as_posix(),
            'manifest_sha256': digest(run / 'manifest.json'),
            'schema_version': manifest['schema_version'],
        }, indent=2) + '\n')
        atomic_copy(pointer, reports / 'current.json')
    except Exception:
        for relative in reversed(published):
            if previous[relative]:
                atomic_copy(run / 'before' / relative, root / relative)
            else:
                (root / relative).unlink()
        raise
    finally:
        lock.unlink()


def run_build():
    root = paths.ROOT
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8]
    run = root / 'data' / 'runs' / run_id
    run.mkdir(parents=True)
    source = run / 'source'
    source.mkdir()
    for folder, pattern in [('xray', '*.py'), ('tests', '*.py'), ('scripts', '*.sh')]:
        for file in sorted((root / folder).rglob(pattern)):
            target = source / file.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target)
    (source / 'requirements.txt').write_text(f'duckdb=={duckdb.__version__}\n')
    inputs = {spec.filename: digest(paths.RAW_DIR / spec.filename) for spec in TABLES.values()}
    environment = dict(os.environ, XRAY_WORKSPACE=str(run), XRAY_PROJECT_ROOT=str(root),
                       XRAY_RAW_DIR=str(paths.RAW_DIR), PYTHONDONTWRITEBYTECODE='1',
                       PYTHONPATH=str(source))
    print(f'Construccion aislada: {run}', flush=True)
    subprocess.run([sys.executable, '-B', '-m', 'xray.cli', '_build'],
                   cwd=source, env=environment, check=True)
    print('Verificando artefactos y regresiones antes de publicar', flush=True)
    verification = subprocess.run(
        [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
        cwd=source, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    (run / 'verification.log').write_text(verification.stdout)
    print(verification.stdout, end='', flush=True)
    verification.check_returncode()
    if inputs != {spec.filename: digest(paths.RAW_DIR / spec.filename) for spec in TABLES.values()}:
        raise ValueError('Los CSV cambiaron durante la construccion; no se publica')
    files = [file for folder in ('data/interim', 'data/clean', 'data/marts', 'reports/quality')
             for file in sorted((run / folder).glob('*')) if file.is_file()]
    git = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    manifest = {
        'schema_version': 2,
        'run_id': run_id,
        'git_revision': git.stdout.strip() if git.returncode == 0 else None,
        'python': platform.python_version(),
        'dependencies': {'duckdb': duckdb.__version__},
        'parameters': {'fx_policy': 'unknown_no_estimates', 'first_month': str(FIRST_MONTH),
                       'dataset_end': str(DATASET_END),
                       'pattern_policy': 'same_company_direction_prior_months'},
        'inputs': inputs,
        'source': {file.relative_to(source).as_posix(): digest(file)
                   for file in sorted(source.rglob('*')) if file.is_file()},
        'outputs': {file.relative_to(run).as_posix(): digest(file) for file in files},
        'verification': {'command': 'python -B -m unittest discover -s tests -v',
                         'exit_code': 0, 'log_sha256': digest(run / 'verification.log')},
    }
    (run / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    publish_run(root, run, manifest)
    print(f'Publicada {run_id}; originales conservados en {run / "before"}', flush=True)
    return run
