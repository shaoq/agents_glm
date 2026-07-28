## Context

The first orchestrator change delivered the domain model, durable Task Runtime, planning, capability routing,
evidence, gates, reporting, CLI, and persistence as independently tested components. The production call path,
however, is currently:

```text
CLI → OrchestrationService → RuntimeWatch → RuntimeTick → ready Tasks
```

`start_run` persists a Run in `NORMALIZING`; `RuntimeTick` only dispatches already-materialized Tasks. Goal
normalization, planning, evidence join, analysis, writing, review, and finalization are invoked manually by
tests rather than by the public application path. Consequently, a caller cannot submit only a goal and receive
the promised report artifacts.

The existing architectural constraints remain:

- deterministic components own formal state transitions and acceptance;
- model-backed components emit Proposals only;
- capability calls occur outside write transactions and pass through the router;
- SQLite is the durable source of truth and artifacts are immutable;
- Run, Task, Attempt, and Operation identities remain distinct;
- the first release remains local-first, read-only, and single-process.

## Goals / Non-Goals

**Goals:**

- Provide one public create-and-drive flow from raw goal to terminal or explicitly blocked Run.
- Add a bounded, state-driven Run coordinator above the existing Task Runtime.
- Connect all existing orchestration components through production composition rather than test helpers.
- Make every phase restart-safe, idempotent, observable, and subject to policy, deadline, and budget.
- Resume Human Gates at a persisted continuation point.
- Align Python API, CLI, Runtime Watch, documentation, and end-to-end tests.

**Non-Goals:**

- Replacing SQLite, the Domain model, Capability Router, Worker Executor, or Task Runtime.
- Adding distributed workers, multiple Watch processes, FastAPI, Web UI, or multi-tenancy.
- Adding write-side-effect capabilities.
- Building the deferred evaluation platform.
- Reworking Memory or RAG public APIs.
- Allowing model output to perform state transitions or bypass deterministic validation.

## Decisions

### 1. Add a RunCoordinator above the Task Runtime

The application SHALL introduce a `RunCoordinator` whose `advance(run_id)` method executes at most one bounded
semantic step. It selects work from the persisted Run state and returns an `AdvanceReport`.

```text
CLI / Python API
        │
        ▼
OrchestrationService
        │
        ▼
RunCoordinator.advance
        │
        ├── NORMALIZING ──────────────── Goal phase
        ├── PLANNING ─────────────────── Plan phase
        ├── RESEARCHING ──┐
        ├── ANALYZING ────┼───────────── phase-aware Task Runtime
        ├── WRITING ──────┤
        ├── REVIEWING ────┘
        └── FINALIZING ───────────────── Completion + Finalizer
```

`RuntimeTick` remains the execution engine for eligible Tasks. It is not expanded into a monolithic workflow
engine. Internally it should be named or described as `TaskRuntimeTick` to make this boundary explicit.

**Why:** Run lifecycle coordination and Task attempt execution have different invariants. Keeping them separate
preserves the existing lease/fencing/retry unit while making the missing outer lifecycle explicit.

**Alternatives considered:**

- Put every phase in `RuntimeTick`: fewer classes, but conflates workflow state, model proposal acceptance, and
  Task execution.
- Put orchestration directly in CLI: violates the Application boundary and prevents Python/API reuse.
- Model the outer lifecycle as an unrestricted dynamic graph: weakens the fixed, auditable phase contract.

### 2. Use one bounded advance protocol

`advance` returns:

```text
AdvanceReport
  run_id
  from_state
  to_state
  disposition = PROGRESSED | BLOCKED | IDLE | TERMINAL
  reason
  state_version
  task_tick_report?
```

Meanings are strict:

- `PROGRESSED`: durable state, accepted result, or formal work changed.
- `BLOCKED`: a known external condition prevents progress, such as an open Gate or explicit Pause.
- `IDLE`: no work completed in this call, but the Run is not semantically blocked; Watch may poll again.
- `TERMINAL`: the Run is succeeded, failed, or canceled.

`RuntimeWatch` loops `advance`, not raw Task ticks. It stops only on `BLOCKED`, `TERMINAL`, configured limits,
or process cancellation. A Task tick dispatch count of zero is not itself proof that a Run is blocked.

**Why:** The current Watch converts “no Task dispatched” into blocked, which prevents phases that do not begin
with a pre-existing Task from progressing.

### 3. Route phases deterministically from RunState

The coordinator owns a fixed dispatch table:

| Run state | Eligible action | Durable success result |
|---|---|---|
| `CREATED` | initialize execution | `NORMALIZING` |
| `NORMALIZING` | normalize goal and completion | Goal/Contract stored; `PLANNING`, or clarification Gate |
| `PLANNING` | propose, validate, and accept plan | Plan/Tasks stored; approval Gate or `RESEARCHING` |
| `RESEARCHING` | execute research Tasks and join evidence | EvidenceSet stored; `ANALYZING`, Replan, Gate, or termination |
| `ANALYZING` | execute Analyst Tasks | AnalysisArtifact stored; `WRITING` or focused research |
| `WRITING` | execute ReportWriter Tasks | Report Draft stored; `REVIEWING` |
| `REVIEWING` | execute Reviewer Tasks and apply verdict | revision, Replan, Gate, or `FINALIZING` |
| `FINALIZING` | verify completion and build artifacts | terminal Run plus final checkpoint |

Waiting and paused states never execute phase work. Gate responses and Resume commands determine the next
allowed phase from persisted continuation data.

### 4. Keep proposal generation outside write transactions

Each phase follows a prepare/execute/accept protocol:

1. In a short read transaction, capture the Run, state version, plan version, policy, and immutable input refs.
2. Outside a write transaction, invoke the model, capability, or worker.
3. In a write transaction, verify the captured versions and input hashes.
4. Deterministically validate the result.
5. Atomically persist accepted outputs, Event, Checkpoint, stage record, and state transition.

Stale results become observations and MUST NOT advance the Run.

**Why:** This extends the existing Attempt fencing rule to phase-level proposals and prevents long model calls
from holding SQLite write locks.

### 5. Persist stage execution records

A durable `StageExecution` record SHALL contain:

- stable stage execution ID and Run ID;
- phase and logical stage key;
- input state/plan/contract versions and input artifact hashes;
- status (`PREPARED`, `ACCEPTED`, `REJECTED`, `FAILED`, `SUPERSEDED`);
- accepted output artifact refs or entity IDs;
- attempt count, failure category, and timestamps;
- idempotency key.

There may be only one accepted result for the same logical stage key and input fingerprint. A restart first
reuses that accepted result instead of invoking the provider again.

**Why:** Run state alone identifies the phase but not whether an external phase call completed before a crash.
The record closes the crash window without storing large payloads in SQLite.

### 6. Execute phase-eligible Worker roles only

The accepted Plan may contain specialized Worker Tasks, but Task readiness is filtered by the current outer
phase:

```text
RESEARCHING → EVIDENCE_RESEARCHER
ANALYZING   → ANALYST
WRITING     → REPORT_WRITER
REVIEWING   → REPORT_REVIEWER
```

`RESEARCH_PLANNER` is invoked by the Goal/Planning phase ports and is not a normal research Task. Plan
validation SHALL require the role coverage and dependencies needed to reach the configured deliverables.

The Task Runtime retains Attempt, Lease, retry, budget, and late-result behavior. The coordinator transitions
to the next phase only when all required Tasks for the active phase have an accepted terminal outcome and no
relevant Attempt remains in flight.

### 7. Pass phase outputs through immutable artifacts

The canonical handoffs are:

```text
GoalSpec + CompletionContract
  → accepted Plan
  → accepted research results
  → EvidenceSet Artifact
  → AnalysisArtifact
  → Report Draft Artifact
  → Review Proposal Artifact
  → report.md + report.json + run-summary.json
```

Stage records store refs and hashes, not embedded large content. The accept transaction binds every output to
the state, plan, contract, and input artifact versions that produced it.

### 8. Make review decisions deterministic and bounded

The Reviewer still emits only `PASS`, `REVISE`, `RESEARCH_GAP`, `CONFLICT`, or `ESCALATE`.
The coordinator maps the verdict through policy:

- `PASS` → optional final review Gate, otherwise `FINALIZING`;
- `REVISE` → `WRITING` when revision budget remains;
- `RESEARCH_GAP` → focused Replan when Replan budget remains;
- `CONFLICT` or `ESCALATE` → configured Gate, partial result, or failure;
- exhausted bound → configured partial/fail/escalate action.

Retry, Replan, and report revision counters remain monotonic and are never reset by phase transitions.

### 9. Persist Gate continuation instead of accepting arbitrary resume targets

Every Gate SHALL include a `GateContinuation` with:

- origin phase and state/plan/contract version;
- allowed response outcomes;
- deterministic next state or action for each outcome;
- bound candidate plan or artifact hash where applicable.

Consuming a Gate applies its continuation atomically. `run resume` resumes only an explicitly paused Run and
uses a persisted pause continuation; callers no longer select an arbitrary target phase. Goal clarification
and conflict Gates may keep the Run in their origin phase while the open Gate provides the blocking condition;
plan approval and final review continue to use their explicit waiting states.

### 10. Add a complete application composition root

Production construction SHALL explicitly provide:

- Goal Normalizer and Planner ports;
- role-specific EvidenceResearcher, Analyst, ReportWriter, and ReportReviewer handlers;
- Capability Registry and Router;
- Plan Validator/Acceptor and Replan service;
- Evidence Joiner;
- Gate service;
- Completion Evaluator, Citation Validator, Report Builder, and Finalizer;
- RunCoordinator and Task Runtime.

The default test composition uses deterministic Fake ports. Production configuration must not silently fall
back to Fake implementations when a real profile is requested.

### 11. Align public control semantics without an immediate Python break

The canonical service operations become:

```text
create_run(command)               # persist CREATED only
start_and_drive(command)          # create + drive until blocked/terminal
advance_run(run_id)               # one RunCoordinator step
drive_run(run_id)                 # loop coordinator
resume_and_drive(run_id, event)   # consume pause/gate continuation + drive
```

The existing synchronous `start_run(raw_goal, request_id=...)` remains temporarily as a deprecated
creation-compatible wrapper so current callers do not break. CLI behavior follows the documented contract:

- `run start` calls `start_and_drive`;
- `--create-only` calls `create_run`;
- `--follow` changes event presentation only;
- `runtime tick RUN_ID` calls one coordinator advance;
- `runtime watch` loops coordinator advances.

### 12. Define success through the public boundary

The primary E2E test SHALL:

1. construct the application with Fake ports;
2. submit only a raw Goal through `start_and_drive`;
3. avoid direct calls to PlanAcceptor, ReplanService, ReportBuilder, or Finalizer;
4. reach a terminal Run;
5. verify state/event/checkpoint history;
6. verify `report.md`, `report.json`, and `run-summary.json` hashes and content.

Component-level manual composition tests may remain, but they no longer count as top-level E2E coverage.

## Risks / Trade-offs

- **[Coordinator becomes a god object]** → Use a small dispatcher plus phase handlers with explicit input/output
  contracts; keep policy and state rules in existing deterministic services.
- **[Duplicate provider calls after a crash]** → Persist StageExecution prepare/accept records and idempotency
  keys; accept only matching versions and hashes.
- **[Old Runs lack stage records]** → Reconstruct the next logical stage from Run state and existing
  Plan/Task/Artifact data, then lazily create records.
- **[Current plans omit later phase roles]** → Reject new incomplete plans; migrate or replan non-terminal old
  Runs before driving them.
- **[Runtime command meaning changes]** → Document that CLI `runtime tick` is a Run-level advance while
  TaskRuntimeTick remains an internal execution unit.
- **[Real model adapters produce invalid outputs]** → Keep schema parsing, deterministic validation, bounded
  retries, structured failure, and Fake-based default tests.
- **[More SQLite transactions]** → Keep them short, never call providers inside writes, and use CAS to handle
  concurrency.

## Migration Plan

1. Add StageExecution and continuation persistence with forward-only SQLite migrations.
2. Introduce phase ports, handlers, coordinator, and coordinator tests without changing CLI routing.
3. Wire TaskRuntimeTick through the coordinator and add phase-role filtering.
4. Add Fake and production composition roots; verify no sibling project dependency reversal.
5. Add new application operations while preserving the deprecated creation-compatible `start_run`.
6. Switch CLI and Runtime Watch to the coordinator.
7. Replace the manual top-level E2E with public-boundary E2E and retain useful component tests.
8. Update README and architecture status after all compatibility and recovery tests pass.

Rollback keeps the new tables unused and routes CLI back to the prior Task Runtime driver. Because migrations
are additive and accepted artifacts remain immutable, rollback does not require deleting Run history.

## Open Questions

None required before implementation. Exact module filenames may follow existing package conventions, but the
RunCoordinator/TaskRuntime boundary and public semantics above are fixed by this proposal.
