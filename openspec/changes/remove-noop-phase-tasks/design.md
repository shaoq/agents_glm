## Context

The orchestration runtime distinguishes two execution paths (documented in `llm_ports.py`):
- **Coordinator-owned phase ports** — `GoalNormalizer`, `Planner`, `LLMAnalyst`, `LLMReportWriter`, `LLMReportReviewer`: bounded calls invoked and accepted by phase handlers; provider failures degrade the phase to IDLE and are governed by coordinator retry/replay policy.
- **Independently scheduled Tasks** — research evidence collection via external capabilities: dispatched through the Task Runtime with Lease/Attempt/fencing/retry.

RESEARCH correctly uses the independently scheduled Task path. But ANALYZE/WRITE/REVIEW were left straddling both: their real work runs in phase ports, yet the Planner still emits per-role Tasks and `_NoopHandler` runs each through the full Task Runtime only to mark it SUCCEEDED. The Task records neither contain nor protect the real model calls. This creates three empty runtime round-trips and makes a control-flow helper, rather than domain semantics, dictate Plan shape.

The current runtime has a separate safety concern: `eligible_worker_roles(...) is None` means "do not filter ready Tasks", not "dispatch nothing". Removing `PHASE_ROLES` entries without a hard state guard would therefore weaken scheduling safety. This design makes the invariant explicit in `RuntimeTick`.

## Goals / Non-Goals

**Goals:**
- Make ANALYZE/WRITE/REVIEW call their coordinator-owned phase port directly (align with GOAL/PLAN/FINALIZE).
- Make new Plans and Replans contain only independently dispatchable research Tasks.
- Enforce at the Task Runtime boundary that only `RESEARCHING` may schedule Tasks.
- Safely reconcile legacy non-research Tasks without losing terminal audit history.
- Preserve phase-order visibility and define PLAN_APPROVAL as approval of the dynamic research dispatch plan.
- Preserve prerequisite gating (via the Run state machine) and the `WorkerRole` enum (model-profile routing).

**Non-Goals:**
- No change to research Task multi-source / Lease / retry mechanics (the independently scheduled path is retained).
- No cross-phase persistence of EvidenceSet / AnalysisArtifact / ReportContent (existing deferred work; separate change).
- No change to the Run state machine or the GOAL/PLAN/FINALIZE handlers.
- No shrinking of the `WorkerRole` enum.
- No removal of ANALYST/REPORT_WRITER/REPORT_REVIEWER `WorkerDefinition` registrations.

## Decisions

1. **ANALYZE/WRITE/REVIEW execute via direct port call.** Their `execute` invokes the phase port and returns a PROGRESSED outcome, mirroring `GoalPhaseHandler`. Provider failure continues to return IDLE and StageExecution/input-fingerprint rules continue to govern replay and stale acceptance; Task Attempt retry is not used for these calls. *Rationale:* these fixed phase calls are coordinator-owned and the existing noop Task does not lease, retry, or fence the real call. *Alternative considered:* run the real port inside a Task — rejected because it changes the phase-port ownership model and preserves unnecessary task machinery.

2. **`PlanGraph` is the dynamic research dispatch plan.** The Planner emits only `evidence_researcher` TaskSpecs. ANALYZE→WRITE→REVIEW→FINALIZE is a fixed lifecycle described by state-machine/control-surface metadata rather than synthetic Tasks. PLAN_APPROVAL approves the dynamic research scope while still identifying the fixed downstream lifecycle. The fixed Writing phase owns `report.md`; any other CompletionContract deliverable must still be covered by the Plan. *Alternative considered:* retain declarative no-op plan entries — rejected because they are not independently schedulable work and conflate lifecycle visibility with Task execution.

3. **Research-only Plan shape is enforced deterministically.** `_TaskSpecOut.role` accepts only `evidence_researcher`; unknown roles fail model-output validation instead of falling back to research. `PlanValidator` rejects any non-research TaskSpec, and Replan validates all added TaskSpecs before saving or transitioning any current Task, Plan, Run, dependency, or event. Replan excludes legacy non-research Tasks from preservation. *Rationale:* a MUST-level orchestration boundary cannot depend on prompt compliance or one Planner implementation, and rejected input must not partially mutate the current Plan version.

4. **`RuntimeTick` has a hard durable-state guard.** It may schedule Tasks only when `run.state is RESEARCHING`, and then only `EVIDENCE_RESEARCHER`. Every other RunState produces zero dispatches even if ready or legacy Tasks exist. `eligible_worker_roles` returns an explicit empty set for active non-Task phases; `None` is not treated as an implicit empty set. *Rationale:* defense in depth keeps raw/internal Tick use safe and makes the requirement independent of which PhaseHandler normally calls it.

5. **Prerequisite gating stays on durable Run state.** Entering ANALYZING proves that the RESEARCHING phase was accepted after required research Tasks succeeded and the evidence join validated. The analyst reloads accepted evidence records from persistence. WRITING/REVIEWING similarly rely on accepted Run transitions and their input providers. This change does not claim that `EvidenceSet`, `AnalysisArtifact`, or `ReportContent` is newly persisted across phases. *Rationale:* the noop Tasks carry no prerequisite signal beyond the already accepted transition.

6. **Remove noop execution bindings, retain role metadata.** `_NoopHandler` and its three Task handler registrations are removed from both composition roots. `WorkerRole` and all current `WorkerDefinition` registrations remain unchanged because they also describe actor/model capability metadata; redefining or splitting `WorkerRegistry` is outside this change. *Alternative considered:* remove the three definitions — deferred until the registry is explicitly made dispatch-only; removing them now creates inconsistent treatment of `RESEARCH_PLANNER` and a larger compatibility surface.

7. **Delete `_drive_phase_tasks`.** `ResearchPhaseHandler` already owns its Task-driving logic directly, so removing the three noop consumers leaves no caller. *Rationale:* retaining a zero-caller or duplicate research helper adds ambiguity without reducing churn.

8. **Legacy Plans are reconciled lazily and atomically.** On the first post-upgrade advance of an affected Run, non-research Tasks in the current Plan are classified before direct phase execution: existing terminal history remains unchanged; PENDING/READY Tasks become SKIPPED; DISPATCHED/AWAITING_RETRY Tasks become CANCELED and their active leases are invalidated so late results cannot be accepted. Replan excludes these Tasks from the next version. The reconciliation emits auditable task/phase events and commits before advancing. *Alternative considered:* require a deployment-wide data migration — rejected because the schema is unchanged and lazy reconciliation is bounded per active Run. Deployments that can prove there are no active Runs may skip the data update, but behavior remains safe if legacy rows exist.

9. **Observability moves from noop Task events to phase records.** ANALYZE/WRITE/REVIEW progress is represented by StageExecution, checkpoints, phase failure diagnostics, and Run transitions. No Task/Attempt/Lease event is emitted for those roles after reconciliation. *Rationale:* audit records should describe the real unit of work.

## Risks / Trade-offs

- [Plan shape and approval semantics are breaking] → document `PlanGraph` as a research dispatch plan and expose the fixed downstream lifecycle without synthetic Tasks.
- [Legacy non-research Tasks may be ready or leased] → hard-gate RuntimeTick first, then atomically skip/cancel and invalidate leases before direct phase advancement.
- [Direct phase calls do not gain Task retry/fencing] → preserve existing IDLE, StageExecution, fingerprint, retry-budget, and stale-result behavior and test it explicitly.
- [Task-level progress events disappear] → assert equivalent Stage/Run observability and update CLI/README expectations.
- [Four phases are no longer symmetric] → intended: RESEARCH is independently scheduled; the others are coordinator-owned.
- [`analysis_provider` / `report_provider` double LLM call (existing deferred work)] → out of scope; this change neither fixes nor worsens it.
- [`add-orchestration-llm-ports` still has smoke/README work] → declare the dependency and coordinate overlapping documentation edits.

## Migration Plan

1. Land the RuntimeTick `RESEARCHING` hard guard before removing any handler binding.
2. Land deterministic Planner/PlanValidator/Replan role enforcement.
3. Add lazy legacy-task reconciliation and verify old-shaped active Plans recover without dispatching a phase Task.
4. Switch ANALYZE/WRITE/REVIEW to direct phase calls and remove `_NoopHandler` registrations.
5. Update approval/control-surface documentation and observability tests.

No schema migration is required. Existing Task rows are reconciled by state as described above. Rollback is safe only before legacy reconciliation, or via a forward-compatible revert that tolerates SKIPPED/CANCELED phase Tasks; a blind `git revert` must not assume noop Tasks can be recreated for already-advanced Runs.

## Dependencies

- `add-orchestration-run-coordinator` defines the modified `run-lifecycle-coordination` capability and must be archived first.
- `add-orchestration-llm-ports` provides the real phase ports and must be implementation-complete before this change is archived; its README edit must be coordinated.

## Open Questions

None. The runtime state guard, WorkerDefinition retention, helper deletion, and legacy reconciliation strategy are resolved by this design.
