from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[6]
OUT = Path(__file__).resolve().parent
DELIVERY = OUT.parent
CACHE = ROOT / 'apps/app/tsconfig.tsbuildinfo'
SOURCES = tuple(json.loads((DELIVERY / 'source-after-blocked.json').read_text())['sha256']) + (
    'scripts/financial_v3_dataset_delivery_amendment.py', 'tests/test_financial_v3_dataset_delivery_amendment.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    return {p: sha(ROOT / p) for p in SOURCES if (ROOT / p).is_file()}


def cache():
    if CACHE.is_symlink() or not CACHE.is_file():
        raise ValueError('Cache must be a regular nonsymlink file')
    return {'sha256': sha(CACHE), 'bytes': CACHE.stat().st_size, 'version': json.loads(CACHE.read_text())['version']}


label, *command = sys.argv[1:]
if not label.replace('-', '').isalnum() or not command:
    raise ValueError('Expected unique command label and argv')
target = OUT / ('command-' + label + '.json')
if target.exists():
    raise FileExistsError(target)
before, cache_before = sources(), cache()
next_env_before = sha(ROOT / 'apps/app/next-env.d.ts')
started = datetime.now(timezone.utc).isoformat()
env = {k: os.environ[k] for k in ('FINANCIAL_V3_ASSESSMENT_FILE', 'TRAJECTORY_ASSESSMENT_FILE', 'EXPORTED_ASSESSMENT_FILE', 'CARGO_NET_OFFLINE', 'NEXT_TELEMETRY_DISABLED', 'PYTHONDONTWRITEBYTECODE') if k in os.environ}
result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
after = sources()
record = {'command': command, 'cwd': str(ROOT), 'started_at': started, 'exit_code': result.returncode,
    'environment_allowlist': env, 'source_before_sha256': before, 'source_after_sha256': after,
    'source_unchanged': before == after, 'cache_before': cache_before, 'cache_after': cache(),
    'next_env_observation': {'before_sha256': next_env_before, 'after_sha256': sha(ROOT / 'apps/app/next-env.d.ts'),
        'exception': False, 'note': 'Typegen/build and dev generate different references. Final amended verification still requires exact original next-env bytes; no second exception.'},
    'stdout': result.stdout, 'stderr': result.stderr}
with target.open('x') as handle:
    json.dump(record, handle, indent=2, sort_keys=True)
    handle.write('\n')
print(result.stdout + result.stderr)
print(json.dumps({'record': str(target.relative_to(ROOT)), 'exit_code': result.returncode,
    'source_unchanged': before == after, 'cache_changed': record['cache_before'] != record['cache_after']}))
raise SystemExit(result.returncode if before == after else 90)
