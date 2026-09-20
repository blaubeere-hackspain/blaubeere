"""Local smoke-test/adapter interface. This is not an HTTP server."""
import argparse
import sys
from pathlib import Path

from scripts.financial_v3_contract import canonical_bytes

from .bundle import Bundle
from .contract import MAX_BYTES, InputError, decode
from .runtime import assess
from .schema import input_schema, output_schema


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--bundle-sha256', help='Trusted manifest SHA256 supplied by the release controller')
    parser.add_argument('--input', type=Path, help='Snapshot JSON; stdin when omitted')
    parser.add_argument('--schema', choices=('input', 'output'))
    args = parser.parse_args(argv)
    if args.schema:
        value = input_schema() if args.schema == 'input' else output_schema()
        print(canonical_bytes(value).decode())
        return 0
    if args.bundle is None or args.bundle_sha256 is None:
        parser.error('--bundle and --bundle-sha256 are required')
    try:
        bundle = Bundle.load(args.bundle, expected_sha256=args.bundle_sha256)
    except (OSError, ValueError, TypeError, KeyError):
        print('{"error":"bundle_unavailable","status":503}', file=sys.stderr)
        return 1
    try:
        if args.input is None:
            raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        else:
            with args.input.open('rb') as stream:
                raw = stream.read(MAX_BYTES + 1)
        value = assess(decode(raw), bundle)
    except InputError as error:
        print(canonical_bytes({'error': error.code, 'status': error.status}).decode(), file=sys.stderr)
        return 1
    except OSError:
        print('{"error":"input_unavailable","status":400}', file=sys.stderr)
        return 1
    print(canonical_bytes(value).decode())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
