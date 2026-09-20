# Predictive model in the company dashboard

The first production integration evaluates the selected `predictive-model-1` estimators **inside the Rust API**. The two estimators are frozen JSON persistence tables; their only branching inputs are current operating receipts and their trailing three-month average. A separate Python server or VM is unnecessary for this release.

This is a scoped alternative to T3–T7 of the [raw-snapshot service plan](xray-prediction-model-jio-plan.md), not completion of that service. The Python snapshot runtime remains available for future ingestion of new transaction-level evidence. The existing v4 health score and daily cash calculations remain separate.

## Data and API

1. The existing `import-parquet` command verifies the predictive manifest, models, index and all 1,078 published profile hashes. It checks company/group identities against the v4 dataset.
2. It imports 25,872 company/month feature snapshots into the same immutable analytics SQLite database as `predictive_inputs`. Only features and provenance are imported, not precomputed prediction outputs or evaluation labels.
3. Rust applies the pinned JSON estimators on each assessment request. Results appear in `records[].predictive` through the existing authenticated `/api/companies/{id}/assessment` route and the explicitly published dataset demo. Existing company authorization still runs before private data access.
4. The dashboard’s **Receipt outlook** follows the selected company and month. It shows 30/60/90-day checkpoints with separate forecasts explicitly unavailable, then the combined contraction and expansion probabilities for the exact three-calendar-month horizon, unavailable reasons and the model’s limitations.

`receipt-outlook-1` identifies the native response. It is not the Python `predictive-assessment-1` raw-snapshot contract. Each result includes model/implementation identifiers, model/manifest hashes, an input hash, cutoff, horizon, calibration status and reasons. The selected models and exclusion index are compiled into the Rust release and hash-checked at startup. No new dependency, external service, environment secret, training or Python runtime is required.

Only imported monthly inputs are supported in this integration. A newly connected account does not automatically acquire predictions; it needs properly prepared and versioned inputs first. Archived inputs have no verified historical `known_on`, so responses are labelled `reconstructed_retrospective`. These are newly computed interpretations of historical data, not predictions issued at the historical cutoff.

## Meaning and limits

- Contraction: receipts at least 20% below the three-month baseline in at least two of the following three months.
- Expansion: receipts at least 20% above that baseline in at least two of the following three months.
- These are separate binary events, not complementary probabilities or predicted cash amounts.
- The conditional temporary-dip model remains unavailable. No fabricated fallback or zero replaces null.
- Dates at/before the December 2025 model cutoff, open months, missing inputs and all original excluded companies/groups abstain.
- The probabilities are uncalibrated and predictive utility gates have not passed. The UI discloses that accuracy is unvalidated. Automatic alerts remain disabled.
- There is no Benford’s law implementation in this change.

## Deployment and checks

The existing main-branch Jio workflow builds the model into the API/MCP binaries and includes the hash-verified source artifacts in the app release. It imports a fresh immutable database before activation. Dataset schema stays at 3; the import batch version changes so old databases cannot masquerade as populated predictive imports. Prior code can still read its previous snapshot during rollback.

The workflow uses the existing verified `blaubeere` account and application VM. No second VM is provisioned. The production smoke check verifies a known company’s computed probability, exact horizon, input count, pre-cutoff abstention and unavailable dip model over HTTPS.

```sh
cargo test --locked --workspace
cargo clippy --locked --workspace --all-targets -- -D warnings
bun run test:deploy
bun run test:web
bun run typecheck
bun run build
bun scripts/check-receipt-outlook.ts http://127.0.0.1:3150
```

The Rust parity test checks all 12,578 published non-null target outputs within `1e-12`, plus every archived null result, cutoff/exclusion boundaries and foreign-company input rejection. The API import test checks the full dataset import and company access. These are implementation checks, not new outcome evaluation or predictive validation.

To change the model, publish new scientific artifacts and update the pinned hashes, supported estimator and parity checks together. Do not retrain during deployment or accept arbitrary weights from requests. A model requiring the full Python inference runtime can use the separate service plan when that need becomes concrete.
