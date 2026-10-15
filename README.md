# MarTech Migration Assurance

**Can we replace a customer platform without losing data meaning—and still roll back after the new system receives writes?**

This executable lab migrates a small synthetic customer platform between two different relational schemas. It captures changes during a consistent snapshot, proves semantic parity, blocks unsafe cutover, and reverses post-cutover changes before moving writes back.

**Start with the [generated migration report](docs/evidence/report.md).** The most revealing failure has equal record counts and matching log positions: a destination turns an unknown subscription into false. Reconciliation identifies the exact field and blocks the switch. A separate experiment shows why rollback must stop when the new platform accepts a lifecycle value the old one cannot represent.

```mermaid
%%{init: {"theme":"base","themeVariables":{"background":"#10101D","primaryColor":"#2A2A3D","primaryTextColor":"#F0F0FF","primaryBorderColor":"#7C7CAF","lineColor":"#B8B8FF","textColor":"#F0F0FF","secondaryColor":"#181825","tertiaryColor":"#2E2E45","fontFamily":"IBM Plex Sans, Arial, sans-serif","fontSize":"15px","edgeLabelBackground":"#10101D"}}}%%
flowchart LR
  L[(Legacy native schema)] --> S[Consistent snapshot at watermark W]
  S --> M[Versioned lossless mapping]
  L --> C[Sealed change catch-up after W]
  C --> M
  M --> N[(Modern native schema)]
  L --> V[Independent semantic comparison]
  N --> V
  V --> G{Fresh evidence + write barrier}
  G -->|Parity| R[Route supported writes to modern]
  G -->|Mismatch| B[Block cutover; retain source route]
  N --> Q{Reverse mapping represents new writes?}
  Q -->|Yes: reverse catch-up + parity| K[Route writes back to legacy]
  Q -->|No| X[Block rollback; resume modern]
  classDef stop fill:#2E2E45,stroke:#E3BC7F,color:#F0F0FF,stroke-width:2px;
  classDef source fill:#181825,stroke:#7C7CAF,color:#F0F0FF,stroke-width:1.5px;
  classDef control fill:#2A2A3D,stroke:#B8B8FF,color:#F0F0FF,stroke-width:1.7px;
  classDef passed fill:#2E2E45,stroke:#81C5B0,color:#F0F0FF,stroke-width:2px;
  class B,X stop;
  class L,N source;
  class S,M,C,V,G,Q control;
  class R,K passed;
```

## What runs

The default demonstration uses **six fictional organizations and 120 fictional people**. Legacy and modern platforms have separate SQLite files, native tables, atomic change logs and request receipts. A third small file stores the write route, cutover barrier and evidence history. Python's standard library is the complete runtime dependency set.

The implementation preserves stable source IDs, relationships, Unicode names, lifecycle states, bounded scores, channel sets, three-valued subscription state and a versioned locale field. It does not perform identity resolution, determine legal consent, send campaigns or connect to a real CRM.

| Engineering question | Executed proof |
|---|---|
| Do writes during snapshot export disappear? | A source update/delete/insert commits between export pages; the pinned baseline plus complete suffix restores parity |
| Can schema changes silently lose fields? | Mapping v1 rejects a v2 locale change without advancing its checkpoint; mapping v2 resumes the same batch |
| Can correct counts hide wrong meaning? | Independent SQL catches subscription, lifecycle, channel and score mutations despite equal counts/cursors |
| Can a passing report become stale? | Activation recomputes current evidence and rejects changed data, code or barrier epoch |
| Does an interrupted transfer skip or duplicate data? | Actual subprocess exits before/after commit; reopen and retry preserve data, receipts and checkpoint together |
| Does rollback preserve new-system writes? | Reverse catch-up transfers them before routing back; retry returns the original receipt |
| Is rollback always possible? | A modern-only lifecycle blocks reverse mapping; abort resumes modern without discarding the value |

## Reproduce

Use Python **3.12**, from the extracted repository root:

```bash
python -m venv .venv
# Activate .venv for your shell.
python -m pip install -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
python scripts/verify.py
```

Verification runs lint, dependency checks, software tests, two actual process-crash experiments and the asserted migration demonstration. It regenerates [evidence](docs/evidence/report.md), [test results](docs/evidence/tests.xml), [crash results](docs/evidence/crashes.json) and a [source-hashed verification record](docs/evidence/verification.json). Each run creates a fresh ignored working directory; no existing operator database is reset. No Docker, paid API or cloud account is required.

For just the migration story: `python scripts/demo.py`. The verification command also generates the linked crash evidence. [Operations](docs/operations.md) explains how to inspect and extend individual transitions.

## Scope of the result

Native Windows execution is demonstrated. Hosted GitHub Actions verification also passed on both Windows and Ubuntu for the published build. Validation covers the local migration protocol; live vendor adapters require separate acceptance testing.

The protocol deliberately pauses writes for final reconciliation. It is **not a zero-downtime migration guarantee**. All supported writers use the local coordinator; direct database administrators are outside its fencing boundary. Long snapshots, retained change logs, full in-memory comparison and a single-host router limit scale. Real vendor migration needs adapter-specific snapshot, deletion, retention, transaction and fencing guarantees.

**Engineering boundary:** This implementation addresses preservation of business meaning during platform replacement: reconciling source and target state, proving semantic parity, and making cutover and rollback decisions evidence-bound. It complements the separate problems of governed customer-state execution and commercial reporting, without treating a successful data transfer as proof of a safe migration.

Read the [architecture and mapping contract](docs/architecture.md), [operating procedure](docs/operations.md), and [skeptical review, evidence and limitations](docs/validation.md).

The [finished project handoff](docs/handoff.md) summarizes the selection, executed results and publication record.
