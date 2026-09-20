import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from xray.model_audit import current_source_hashes

OUT = Path(__file__).resolve().parent
PYTHON = '.venv/bin/python'
GENERATED = ('apps/app/next-env.d.ts', 'apps/landing/next-env.d.ts')
COMMANDS = {
    'replay-tests': [PYTHON, '-B', '-m', 'unittest', 'tests.test_replay_model', '-v'],
    'audit': [PYTHON, '-B', '-m', 'xray.model_audit', '--verify'],
    'replay': [PYTHON, '-B', 'scripts/replay_model.py', '--check'],
    'export': [PYTHON, '-B', 'scripts/export_assessments.py', '--check'],
    'jev': [PYTHON, '-B', '-c', "import runpy, socket, sys; from unittest.mock import patch; sys.argv=['scripts/jev_pilot.py','prepare','--offline']; blocked=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network disabled')); guards=[patch.object(socket.socket,'connect',blocked),patch.object(socket.socket,'connect_ex',blocked),patch.object(socket,'create_connection',blocked),patch.object(socket,'getaddrinfo',blocked)]; [g.start() for g in guards]; runpy.run_path('scripts/jev_pilot.py',run_name='__main__')"],
    'check': ['bun', 'run', 'check'],
    'rust-export': ['cargo', 'test', '--workspace', 'actual_export', '--', '--ignored'],
    'e2e': ['bun', 'run', 'test:assessments:e2e'],
    'deploy': ['bun', 'run', 'test:deploy'],
}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def hashes(paths):
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(set(paths)) if p.is_file()}


def files(folder):
    return [p for p in (ROOT / folder).rglob('*') if p.is_file()]


def protected():
    model = json.loads((ROOT / 'reports/modeling/experiment-v1/model_manifest.json').read_text())
    names = [ROOT / 'data' / name for name in model['input_binding']['raw_input_sha256']]
    names += [ROOT / name for name in model['source_sha256']]
    names += [ROOT / name for name in model['input_binding']['source_sha256']]
    names += [ROOT / 'reports/current.json', ROOT / 'data/marts/panel_flujos.parquet', ROOT / 'data/marts/panel_evidencia.parquet']
    run = Path(model['input_binding']['run_path'])
    names += [run / 'manifest.json', run / 'data/marts/panel_flujos.parquet']
    for folder in ('reports/modeling/protocol-v1', 'reports/modeling/experiment-v1', 'reports/modeling/jev-pilot-v1', 'deploy'):
        names += files(folder)
    names += [ROOT / 'reports/modeling/assessment-v1' / name for name in ('companies.json', 'manifest.json')]
    return hashes(names)


def sources():
    paths = [ROOT / name for name in ('package.json', 'bun.lock', 'Cargo.toml', 'Cargo.lock', 'readme.md')]
    paths += list((ROOT / 'scripts').glob('*.ts'))
    paths += [p for p in (ROOT / 'services').rglob('*') if p.suffix in ('.rs', '.toml')]
    for folder in ('apps/app/app', 'apps/app/components', 'apps/app/lib', 'apps/landing/app'):
        paths += files(folder)
    for folder in ('apps/app', 'apps/landing'):
        paths += [p for p in (ROOT / folder).iterdir() if p.suffix in ('.ts', '.mjs', '.json')]
    paths = [p for p in paths if str(p.relative_to(ROOT)) not in GENERATED]
    return current_source_hashes(ROOT) | hashes(paths) | hashes([Path(__file__)])


def write(name, value):
    path = OUT / name
    payload = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()
    if path.exists() and path.read_bytes() != payload:
        snapshot = OUT / 'history' / (path.stem + '.' + sha(path) + path.suffix)
        snapshot.parent.mkdir(exist_ok=True)
        if not snapshot.exists():
            snapshot.write_bytes(path.read_bytes())
    path.write_bytes(payload)


def processes():
    result = subprocess.run(['ps', '-eo', 'pid=,ppid=,stat=,comm='], capture_output=True, text=True, check=True)
    return {int(p): {'parent': int(pp), 'state': state, 'command': name}
            for p, pp, state, name in (line.split(None, 3) for line in result.stdout.splitlines())}


def run(name):
    command = COMMANDS[name]
    record = OUT / 'commands.json'
    entries = json.loads(record.read_text()) if record.exists() else []
    attempt = 1 + sum(item['name'] == name for item in entries)
    log = OUT / f'{name}.{attempt}.log'
    env = os.environ.copy()
    overrides = {'EXPORTED_ASSESSMENT_FILE': str(ROOT / 'reports/modeling/assessment-v1/companies.json')} if name == 'rust-export' else {}
    env.update(overrides)
    source_before = sources()
    generated_before = hashes(ROOT / name for name in GENERATED)
    if name == 'e2e':
        previous = ROOT / 'reports/modeling/assessment-v1/browser-summary.json'
        if previous.exists():
            write('browser-summary.before.json', json.loads(previous.read_text()))
    started = time.monotonic()
    tracked = set()
    with log.open('xb') as stream:
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
        tracked.add(child.pid)
        while child.poll() is None:
            table = processes()
            while True:
                new = {pid for pid, info in table.items() if info['parent'] in tracked}
                if new <= tracked:
                    break
                tracked |= new
            time.sleep(0.1)
    table = processes()
    leftovers = {pid: info for pid, info in table.items() if pid in tracked and not info['state'].startswith('Z')}
    text = log.read_text()
    entry = {'name': name, 'attempt': attempt, 'command': command, 'environment_overrides': overrides,
             'exit_code': child.returncode, 'seconds': round(time.monotonic() - started, 3),
             'log': str(log.relative_to(ROOT)), 'log_sha256': sha(log),
             'source_hashes': source_before, 'source_hashes_after': sources(),
             'sources_unchanged': source_before == sources(),
             'generated_hashes_before': generated_before,
             'generated_hashes_after': hashes(ROOT / name for name in GENERATED),
             'unittest_counts': [int(n) for n in re.findall(r'^Ran (\d+) tests? in', text, re.M)],
             'rust_test_results': re.findall(r'^test result:.*$', text, re.M),
             'surviving_tracked_processes': leftovers}
    entries.append(entry)
    write('commands.json', entries)
    if name == 'replay' and child.returncode == 0:
        write('replay.json', json.loads(text))
    print(json.dumps({key: value for key, value in entry.items() if key not in ('source_hashes', 'source_hashes_after')}, sort_keys=True), flush=True)
    return child.returncode or int(bool(leftovers))


def summary():
    before = json.loads((OUT / 'baseline.json').read_text())
    after = protected()
    commands = json.loads((OUT / 'commands.json').read_text())
    latest = {item['name']: item for item in commands}
    gate = json.loads((ROOT / 'reports/modeling/data_gate.json').read_text())
    browser = json.loads((ROOT / 'reports/modeling/assessment-v1/browser-summary.json').read_text())
    evidence = files('reports/modeling/final-verification')
    evidence += [ROOT / 'reports/modeling' / name for name in ('data_gate.json', 'recheck.json')]
    evidence += [ROOT / command['log'] for command in gate['commands']]
    evidence += [ROOT / 'reports/modeling/assessment-v1/browser-summary.json']
    required = ('replay-tests', 'audit', 'replay', 'export', 'jev', 'check', 'rust-export', 'e2e')
    current_sources = sources()
    passed = all(name in latest and latest[name]['exit_code'] == 0 and latest[name]['sources_unchanged'] and
                 latest[name]['source_hashes'] == current_sources and
                 not latest[name]['surviving_tracked_processes'] for name in required)
    audit_checks = []
    for command in gate['commands']:
        text = (ROOT / command['log']).read_text()
        audit_checks.append({'command': command['command'], 'exit_code': command['exit_code'],
                             'bound_to_current_python_sources': command.get('source_hashes') == gate['current_source_hashes'],
                             'log': command['log'], 'log_sha256': sha(ROOT / command['log']),
                             'unittest_counts': [int(n) for n in re.findall(r'^Ran (\d+) tests? in', text, re.M)],
                             'skipped_tests': len(re.findall(r'^test_.* \.\.\. skipped ', text, re.M)),
                             'csv_tables_ok': len(re.findall(r'"status": "OK"', text))})
    model = json.loads((ROOT / 'reports/modeling/experiment-v1/model_manifest.json').read_text())
    raw_matches = all(after['data/' + name] == value for name, value in model['input_binding']['raw_input_sha256'].items())
    passed = passed and raw_matches and gate['gate_status'] == 'pass_for_approved_retrospective_scope' and browser['status'] == 'passed'
    result = {'status': 'checks_passed' if passed and before == after else 'checks_incomplete_or_failed',
              'scope': 'Verification results only; no declaration of overall goal completion or Jev adoption.',
              'protected_hashes_before': before, 'protected_hashes_after': after,
              'protected_files_unchanged': before == after, 'source_hashes': sources(),
              'source_hash_policy': 'Maintained Python/TS/Rust/config sources; the two exact Next-generated next-env.d.ts paths are separately recorded because build and dev regenerate type import paths. G-DATA source gate unchanged.',
              'generated_files_sha256': hashes(ROOT / name for name in GENERATED),
              'commands': commands, 'audit_gate_status': gate['gate_status'],
              'audit_commands': gate['commands'], 'audit_check_counts': audit_checks,
              'raw_hashes_match_sealed_binding': raw_matches, 'raw_csv_files': 8,
              'correction_history': [{'command': 'bun run check', 'attempt': 1, 'exit_code': 2,
                                      'cause': 'Existing CRLF deploy/jio.sh rejected by bash -n',
                                      'correction': 'Only check-deploy.ts and package.json validator path adapted; both original shell texts syntax-checked after CRLF normalization, invalid LF/CRLF tested, deployment files byte-identical.'},
                                     {'command': 'bun run test:assessments:e2e', 'attempt': 1, 'exit_code': 0,
                                      'cause': 'All 11 steps passed; evidence tracker included Next-generated next-env.d.ts and flagged its expected build/dev type import rewrite as a source change.',
                                      'correction': 'Record both generated declaration hashes separately, preserve first record, rerun final checks with maintained source identity unchanged. No application/economic/G-DATA source changed.'}],
              'browser_status': browser['status'],
              'browser_steps': browser['steps'], 'replay': json.loads((OUT / 'replay.json').read_text()),
              'evidence_sha256': hashes(p for p in evidence if p.name != 'summary.json'),
              'model_training_or_holdout_evaluation_executed': False,
              'test_scope_note': 'The existing unit suite includes synthetic toy fits/guard tests, never a replay of the sealed final holdout.',
              'jev_network_policy': 'Python socket connect/connect_ex/create_connection/getaddrinfo patched to fail before prepare --offline; existing output hashes unchanged.',
              'server_cleanup': 'Tracked descendants checked after each command; no deployment or shared daemon started.'}
    write('summary.json', result)
    print(json.dumps({'status': result['status'], 'summary_sha256': sha(OUT / 'summary.json'),
                      'protected_files': len(before), 'protected_files_unchanged': before == after}))
    return int(result['status'] != 'checks_passed')


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'baseline':
        if (OUT / 'baseline.json').exists():
            raise FileExistsError('Baseline already captured')
        write('baseline.json', protected())
        print('Protected artifact baseline captured')
    elif action == 'summary':
        raise SystemExit(summary())
    else:
        raise SystemExit(run(action))
