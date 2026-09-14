# Reproduction and operating procedure

## Setup

Use Python 3.12. From the extracted repository root:

```bash
python -m venv .venv
```

Activate with `.venv\Scripts\Activate.ps1` in PowerShell, or `source .venv/bin/activate` in bash. Then:

```bash
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
python scripts/verify.py
```

Pinned dependencies are for build, tests and lint only; runtime has no third-party dependency. Verification does not download a model, contact a SaaS endpoint, or require Docker. It checks installed/source parity and dependency consistency before execution. Tests have no infrastructure-dependent skips.

The demonstration creates a unique subdirectory under ignored `work/`; it never deletes or resets another run's files. Evidence is regenerated under `docs/evidence`. The default story seeds six organizations and 120 people. These figures describe this fixture, not a load test. The CLI is an executable local scenario harness; it is not a production administrative API.

Generated lineage IDs and timestamps vary between executions. Reproduction means the same semantic outcomes and invariants pass; generated evidence files are not promised to be byte-identical across runs.

## Inspect an individual migration

The library exposes the small protocol directly. These operations use a trusted local operator and assume all business writes pass through `Coordinator`:

```python
from migration_lab.coordinator import Coordinator
from migration_lab.fixtures import seed
from migration_lab.snapshot import export_snapshot, import_snapshot
from migration_lab.system import catch_up

lab = Coordinator("work/my-migration")
seed(lab, 12)  # Synthetic initialization; use a new directory.
legacy, modern = lab.systems["legacy"], lab.systems["modern"]
baseline = export_snapshot(legacy)
import_snapshot(modern, baseline)
catch_up(legacy, modern)
lab.freeze("legacy")
catch_up(legacy, modern)
proof = lab.assess()
lab.activate(proof["hash"])  # Recomputes parity; cannot force a failed report through.
```

The demo source shows versioned commands and rollback using the same operations in reverse. Commands use stable caller-provided IDs. Retry the same ID and exact payload when its result is uncertain; a changed payload is a conflict. Optional expected epochs detect stale clients.

## Respond to a blocked transition

| Observation | Action |
|---|---|
| Mapping upgrade required | Keep the destination inactive; inspect the new field contract; use mapping v2 only after reviewing it; replay the same batch |
| Change-log gap / missing retention | Stop transfer; obtain a new valid baseline or restore the retained suffix; never manually advance the cursor |
| Equal counts but field differences | Inspect the field-level report; repair the mapping or inactive replica under a reviewed plan; rerun reconciliation |
| Stale proof | Reassess current data and implementation; do not reuse the old report hash |
| Write barrier remains frozen after interruption | Inspect route/history and both system cursors; continue the migration, or call `resume_original` with the current frozen epoch |
| Legacy cannot represent a modern value | Abort rollback and resume modern; devise a compatible migration plan rather than coercing the value |
| Process exited after destination commit | Retry the identical sealed batch; receipts prove already-applied events |

`resume_original` means retain the system that was active when the barrier was established; after a failed rollback that is modern. It does not undo writes. Never remove database files to make a transition appear successful.

## Evidence and publication

`migration.json` contains exact differences, baseline/gap evidence, checkpoint behavior, cutover/rollback proofs, request receipts and route history. `snapshot.json` is the actual sealed synthetic baseline. `crashes.json` records actual subprocess exit codes and reopened/final cursors. `verification.json` binds the implementation, scripts, tests and CI configuration to the local run.

Before publishing, review the README's limits, run verification from the extracted artifact, inspect the generated reports and review the MIT license. Publish to a new repository yourself; this build does not create a remote or push commits. Observe both configured GitHub Actions jobs before claiming hosted CI success. Preserve failed safety scenarios in the public evidence.

For production adaptation, first validate the proposed vendor adapters' snapshot/log guarantees and fencing capabilities. Add independent identity/access control, secret management, operational ownership, migration approvals, retention sizing, backup/restore tests, capacity tests and business-owner adjudication. These are future requirements, not deployed features.
