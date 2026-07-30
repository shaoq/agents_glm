## Why

The ANALYZE, WRITE, and REVIEW phases already execute their real work through coordinator-owned model-backed phase ports (`LLMAnalyst`, `LLMReportWriter`, `LLMReportReviewer`). Despite this, the Planner still emits `analyst` / `report_writer` / `report_reviewer` TaskSpecs and the composition wires a `_NoopHandler` that runs each one through the full Task Runtime (lease claim, Attempt creation, fencing, checkpoint, events) only to mark it SUCCEEDED, purely so the shared `_drive_phase_tasks` control flow releases the handler to call the port.

This is a half-finished migration from an "all-task dispatch" model to a "coordinator-owned phase call" model. Every Run pays three empty runtime round-trips, while the Task records do not protect, retry, or contain the real LLM work. Task Runtime should be reserved for independently scheduled research work that needs durable Lease/Attempt/retry semantics; fixed lifecycle phases should be driven and observed as phases.

## What Changes

- ANALYZE/WRITE/REVIEW phase handlers call their coordinator-owned phase port directly in `execute` (aligned with `GoalPhaseHandler` / `PlanningPhaseHandler`), no longer gated on a per-role Task being SUCCEEDED.
- LLMPlanner emits only `evidence_researcher` TaskSpecs. Deterministic Plan validation and Replan enforce the same invariant rather than relying on prompt compliance. **BREAKING** — Plan shape changes (no per-role analysis/writing/review tasks).
- Harden `RuntimeTick`: it may schedule Tasks only while the durable Run state is `RESEARCHING`; every other RunState produces zero Task dispatches even if ready or legacy Tasks exist.
- Remove `_NoopHandler` and its ANALYST/REPORT_WRITER/REPORT_REVIEWER Task handler registrations from both composition roots.
- Drop ANALYZE/WRITE/REVIEW entries from `PHASE_ROLES`, with an explicit empty-eligibility contract for non-Task phases; `None` is not used as an implicit "dispatch nothing" signal.
- Delete `_drive_phase_tasks`; `ResearchPhaseHandler` already owns its bounded Task-driving flow directly.
- Keep the `WorkerRole` enum and existing `WorkerDefinition` registrations unchanged. They still describe model/actor roles and support model-profile and capability metadata; only Task handler bindings for the three phase roles are removed.
- Reconcile legacy in-flight Plans: non-terminal analyst/writer/reviewer Tasks are terminalized without dispatch, active leases are invalidated, all historical terminal records remain auditable, and Replan does not carry those Tasks into a new version.
- Define Plan/approval semantics explicitly: `PlanGraph` is the dynamic research dispatch plan; ANALYZE→WRITE→REVIEW→FINALIZE remains a fixed, visible lifecycle and is not represented with synthetic Tasks.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `run-lifecycle-coordination`: "Phase-aware Task execution" is redefined so Task Runtime scheduling is hard-gated to research evidence collection in `RESEARCHING`; new Plans and Replans reject non-research Task roles; ANALYZE/WRITE/REVIEW execute through coordinator-owned phase ports without synthetic Tasks; and legacy no-op Tasks are safely retired.

## Impact

- Code: `runtime/tick.py` (RESEARCHING-only hard gate), `coordination.py` (role eligibility), `planner.py` and `replan.py` (deterministic role enforcement), `phases.py` (3 direct handlers + legacy reconciliation + helper deletion), `composition.py` (`_NoopHandler` + handler bindings), and `llm_ports.py` (research-only schema/prompt with no role fallback). `workers/registry.py`, `adapters/base.py`, and `domain/enums.py` remain unchanged.
- Tests: planner/replan validation, runtime state gating, legacy Plan recovery, direct phase execution/failure/replay, observability, approval semantics, composition, and full E2E coverage.
- API/behavior: PLAN_APPROVAL approves the dynamic research dispatch plan while control-surface documentation also identifies the fixed later lifecycle. Task/Attempt/Lease events for the three no-op roles disappear; phase Stage/Run events remain the audit source.
- Persistence: no schema migration is required, but existing non-terminal no-op Tasks require a one-time or lazy state reconciliation before normal advancement.
- Dependencies: this change modifies the capability introduced by `add-orchestration-run-coordinator` and builds on `add-orchestration-llm-ports`; those changes must be implemented/archived before this delta is archived. Their remaining README work may overlap and must be coordinated.
- Ordering: durable Run state remains the phase-ordering authority. This change does not claim or add cross-phase persistence for `EvidenceSet`, `AnalysisArtifact`, or `ReportContent`; that remains separate work.
