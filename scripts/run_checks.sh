#!/usr/bin/env bash
# T4: chequeos de calidad del dataset xray.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== unittest ==" >&2
.venv/bin/python -B -m unittest discover -s tests -v

echo "== informe de calidad ==" >&2
.venv/bin/python -B -m xray.cli quality
