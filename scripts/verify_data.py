import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def checks():
    for command in (
        [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
        [sys.executable, '-B', '-m', 'xray.cli', 'quality'],
    ):
        print(json.dumps({'command': command}), flush=True)
        try:
            code = subprocess.run(command, cwd=ROOT).returncode
        except OSError as error:
            print(json.dumps({'error': type(error).__name__}), flush=True)
            code = 127
        print(json.dumps({'command': command, 'exit_code': code}), flush=True)
        if code:
            return code
    return 0


def ingest():
    import duckdb

    from xray import paths
    from xray.schema import TABLES

    failed = False
    with duckdb.connect() as con:
        for name, spec in TABLES.items():
            raw = paths.RAW_DIR / spec.filename
            interim = paths.INTERIM_DIR / f'{name}.parquet'
            result = {'table': name, 'raw': str(raw), 'interim': str(interim)}
            try:
                with raw.open(encoding='utf-8', newline='') as stream:
                    records = csv.reader(stream, strict=True)
                    if not next(records, None):
                        raise ValueError('Missing CSV header')
                    result['csv_rows'] = sum(1 for _ in records)
                result['parquet_rows'] = con.execute('SELECT count(*) FROM read_parquet(?)',
                                                     [str(interim)]).fetchone()[0]
                result['status'] = 'OK' if result['csv_rows'] == result['parquet_rows'] else 'MISMATCH'
            except (OSError, ValueError, csv.Error, duckdb.Error) as error:
                result.update(status='ERROR', error=type(error).__name__)
            failed = result['status'] != 'OK' or failed
            print(json.dumps(result), flush=True)
    return int(failed)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('checks', 'ingest'))
    args = parser.parse_args(argv)
    return checks() if args.mode == 'checks' else ingest()


if __name__ == '__main__':
    raise SystemExit(main())
