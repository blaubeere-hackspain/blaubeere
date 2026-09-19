"""Rutas canónicas del repo, derivadas de la ubicación de este fichero."""

import json
import os
from pathlib import Path

ROOT = Path(os.environ.get('XRAY_PROJECT_ROOT', Path(__file__).resolve().parents[1])).resolve()
CURRENT_PATH = ROOT / 'reports' / 'current.json'
if os.environ.get('XRAY_WORKSPACE'):
    WORKSPACE = Path(os.environ['XRAY_WORKSPACE']).resolve()
elif CURRENT_PATH.exists():
    WORKSPACE = (ROOT / json.loads(CURRENT_PATH.read_text())['workspace']).resolve()
    if not WORKSPACE.is_relative_to(ROOT / 'data' / 'runs'):
        raise ValueError('La ejecucion publicada debe estar dentro de data/runs')
else:
    WORKSPACE = ROOT
RAW_DIR = Path(os.environ.get('XRAY_RAW_DIR', ROOT / 'data')).resolve()
INTERIM_DIR = WORKSPACE / 'data' / 'interim'
CLEAN_DIR = WORKSPACE / 'data' / 'clean'
MARTS_DIR = WORKSPACE / 'data' / 'marts'
REPORTS_DIR = WORKSPACE / 'reports' / 'quality'


def ensure_dirs() -> None:
    """Crea los directorios de salida si no existen. Nunca crea RAW_DIR."""
    for d in (INTERIM_DIR, CLEAN_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
