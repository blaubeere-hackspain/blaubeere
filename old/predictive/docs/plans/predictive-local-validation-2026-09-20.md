# Predictive runtime: local validation and production route

Checked on 20 September 2026 against `b54410f` on `main`. `git pull --ff-only` found no newer changes. Scope: the portable package in `services/predictive`, not the historical training/evaluation runner in `scripts/model_inference.py`.

## Result

**The inference package runs correctly locally. It is not yet a deployed service or a validated daily cash forecast.**

| Check | Result |
| --- | --- |
| Python | 3.13.15, macOS arm64; development dependencies from `requirements-model.txt` |
| Runtime and bundle tests | 25 passing, including the added archived-prediction parity check |
| Published company data | 1,078 profiles hash-verified; all 12,578 available target predictions reproduced exactly (6,289 per target), maximum absolute error 0 |
| Unavailable outputs | 65,038 archived null target entries skipped; no predictions generated for excluded companies/groups |
| Portable CLI | Published example succeeds from the release directory with `python -B -S`, with third-party packages disabled |
| Release integrity | Build and read-only `--check` pass; frozen scientific sources and weights unchanged |
| Local volume smoke | 5,001 synthetic records, one warm-up and 20 sequential evaluations: median 1.0292 s, nearest-rank p95 1.1970 s, maximum 1.2075 s |

The volume smoke splits one fixture receipt into separately identified movements/classifications while preserving its amount and date. It measures decoding, assessment and response serialization with an already loaded bundle. Request: 2,844,207 bytes; response: 3,876,351 bytes. This is a synthetic local check, **not** HTTP latency, representative multi-account performance, memory/load testing or acceptance on Jio.

The company-data check replays previously published **features** through the selected estimators. It does not reconstruct raw records, verify historical knowledge availability, score outcomes, retrain, recalibrate or rerun a holdout evaluation. Runtime financial/feature preparation is separately covered by the existing fixture tests.

Validated bundle SHA256: `c0af3d5334d2eaf917cee979938d5b13f6c23994930d0e485070dcc48884585b`.

Validated release-manifest SHA256: `27fe6354f860c7271b48ad42d3714b0ed57259902eebf0467e1d09fb9567b540`.

## What this model supplies

`predictive-model-1` contains two selected persistence estimators: three-month operating-receipt contraction and expansion probabilities. The conditional receipt-dip output remains null. Probabilities are uncalibrated; they are neither future daily cash amounts nor financial-health scores. The fixture produces contraction probability 0.2160891089 and expansion probability 0.1337458746, with the unsupported dip output null.

The existing [training report](../../reports/modeling/training-report.md) records failed predictive utility gates: only 28/345 eligible validation origins have comparable labels, across eight groups, and recall at 0.6 is zero. Passing the technical checks does not change those results. Automatic predictive alerts remain disabled.

## Recommended connection to production

Use the existing [Jio integration plan](xray-prediction-model-jio-plan.md): **Rust authorization → financial snapshot → authenticated HTTPS Python service → versioned assessment returned to Rust**. Keep the current v4 scoring and Rust cash projection separate.

1. **Build the snapshot adapter.** Current `services/api/src/dataset.rs` returns imported score/cash/payment/debt aggregates; `daily_cash.rs` aggregates raw movements and balances into daily cash. These do not preserve the full per-record inputs required by `contracts/predictive/request.schema.json`. Rust needs accounts, movements, dated source evidence, classifications, coverage, obligations, settlements, invoice documents and relevant FX/history. Missing evidence must stay missing. Archived data without verified `known_on` can only support an explicitly labelled retrospective view.
2. **Wrap the existing runtime.** Add the planned HTTP endpoints in `services/predictive`: authenticated `POST /v1/assessments`, authenticated model metadata, liveness and readiness. Load and verify one immutable bundle at startup; run a startup smoke check. Enforce the existing 16 MiB/50,000-record limits, bounded concurrency and sanitized errors. No training or dataset reads per request.
3. **Add the Rust consumer.** Send snapshots only after company authorization, preserve decimal strings and nulls, use a 20-second total deadline and at most one transient retry within that deadline. Bound response reads too: provenance makes responses sizable. Persist input hash, snapshot/model versions and returned assessment. Service failure must remain unavailable, never a zero score or a fabricated forecast.
4. **Deploy and verify separately.** Use an explicitly provisioned model VM, separate `MODEL_JIO_VM_ID`, service token, Python 3.13.15, unprivileged systemd and Jio HTTPS. Verify the complete release before activation; retain rollback. Start with the manual model workflow described in the plan. Check a real Rust request, malformed/unauthorized requests, restart, token rotation, rollback and target-VM p95 before connecting product endpoints.

The package already covers the plan's T0–T2. HTTP, deployment and Rust consumer work (T3–T7) remain. The snapshot adapter is additionally required to connect the current production dataset, beyond the reference consumer in that plan. No service, VM or production endpoint was changed during this validation.

## Repeat locally

From the repository root:

```sh
uv venv --python 3.13.15 .local/predictive-validation/venv-31315
uv pip install --python .local/predictive-validation/venv-31315/bin/python -r requirements-model.txt
mkdir -p .local/predictive-validation/tmp
TMPDIR="$PWD/.local/predictive-validation/tmp" .local/predictive-validation/venv-31315/bin/python -B -m unittest tests.test_predictive_bundle tests.test_predictive_runtime -v
.local/predictive-validation/venv-31315/bin/python -B scripts/build_predictive_bundle.py --output .local/predictive-validation/release
.local/predictive-validation/venv-31315/bin/python -B scripts/build_predictive_bundle.py --output .local/predictive-validation/release --check
```

Use a temp directory without symlink ancestors: macOS's default `/var` temp path is a symlink and the bundle's deliberate symlink rejection otherwise fails the tests. Development parity tests need the locked dependencies; the built runtime does not.

From `.local/predictive-validation/release`:

```sh
../venv-31315/bin/python -B -S -m predictive_model \
  --bundle ./bundle \
  --bundle-sha256 c0af3d5334d2eaf917cee979938d5b13f6c23994930d0e485070dcc48884585b \
  --input contracts/predictive/example.json
```

For later releases, use the hash emitted by that verified build, not this historical hash. Build into a new directory if source files change; the builder intentionally refuses to overwrite a different release.
