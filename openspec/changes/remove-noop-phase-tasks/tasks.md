## 1. Research-only Plan contract

- [x] 1.1 Rewrite `LLMPlanner.propose_plan` for research-only Task emission and constrain `_TaskSpecOut.role` to `Literal["evidence_researcher"]`
- [x] 1.2 Remove analyst/report_writer/report_reviewer Planner role mappings and the unknown-role fallback; invalid model roles must fail structured output validation rather than become research Tasks
- [x] 1.3 Extend `PlanValidator` to reject every non-`EVIDENCE_RESEARCHER` TaskSpec before materialization, with unit tests covering direct/custom Planner proposals
- [x] 1.4 Apply the same role invariant to Replan additions and preserved Tasks; test that a Replan cannot add or carry analyst/report_writer/report_reviewer Tasks into its new version
- [x] 1.5 Define/document `PlanGraph` and PLAN_APPROVAL as the dynamic research dispatch plan while identifying ANALYZE→WRITE→REVIEW→FINALIZE as the fixed downstream lifecycle
- [x] 1.6 Clarify final-deliverable validation and diagnostics so `report.md` is attributed to the fixed Writing phase rather than claimed to be produced by a research Task

## 2. Runtime scheduling hard guard

- [x] 2.1 Harden `RuntimeTick.tick`: only durable `RunState.RESEARCHING` may reach scheduling; every other RunState returns zero dispatches before ready work is claimed
- [x] 2.2 Remove ANALYZE/WRITE/REVIEW entries from `PHASE_ROLES` and define explicit empty role eligibility for active non-Task phases; do not rely on `None` to mean "dispatch nothing"
- [x] 2.3 Unit-test RuntimeTick in ANALYZING, WRITING, REVIEWING, NORMALIZING, PLANNING, FINALIZING, gate-waiting, paused, and terminal states with ready Tasks present; assert no Task, Lease, Attempt, dispatch Event, or dispatch Checkpoint is created
- [x] 2.4 Preserve and regression-test RESEARCHING behavior: only evidence_researcher Tasks dispatch and all existing Attempt/Lease/retry/budget/fencing rules remain active

## 3. Phase handlers — call coordinator-owned ports directly

- [x] 3.1 Rewrite `AnalysisPhaseHandler.execute` to load accepted evidence and call `self.analyst` directly with no Task gate; keep provider failure → IDLE behavior
- [x] 3.2 Rewrite `WritingPhaseHandler.execute` to obtain analysis through its existing input provider and call `self.writer` directly
- [x] 3.3 Rewrite `ReviewPhaseHandler.execute` to obtain the report and call `self.reviewer` directly; preserve `_map_verdict`, loop budgets, and accept behavior
- [x] 3.4 Remove the now-unused RuntimeTick dependency from the three phase-handler constructors and both composition roots
- [x] 3.5 Delete `_drive_phase_tasks` because `ResearchPhaseHandler` already contains the only remaining Task-driving flow; retain `_simple_accept`
- [x] 3.6 Verify direct-call failures and replays remain governed by IDLE/failure diagnostics, StageExecution, input fingerprints, and stale-result acceptance rather than Task Attempt retry

## 4. Remove the noop execution binding

- [x] 4.1 Delete `_NoopHandler` and remove ANALYST/REPORT_WRITER/REPORT_REVIEWER Task handler registrations from both composition roots
- [x] 4.2 Keep all existing `WorkerRole` values and `WorkerDefinition` registrations unchanged; add/adjust tests so registry metadata remains available even though those roles have no Task handler
- [x] 4.3 Verify `select_model_profile` still routes ANALYST, REPORT_WRITER, and REPORT_REVIEWER to their phase-port model profiles

## 5. Legacy Plan reconciliation

- [x] 5.1 Add an idempotent, atomic legacy reconciliation path for current-plan analyst/report_writer/report_reviewer Tasks before direct phase advancement
- [x] 5.2 Preserve all existing terminal history; transition PENDING/READY legacy Tasks to SKIPPED and DISPATCHED/AWAITING_RETRY legacy Tasks to CANCELED using valid state-machine transitions
- [x] 5.3 Invalidate active leases for canceled legacy Tasks and verify late Attempt results are retained only as rejected observations
- [x] 5.4 Emit auditable reconciliation events/checkpoints without manufacturing successful phase results
- [x] 5.5 Integration-test restart/upgrade from an old-shaped Plan in each relevant Task state and verify the Run completes without dispatching a legacy phase Task
- [x] 5.6 Test that reconciliation is idempotent and a later Replan contains only preserved/new research Tasks

## 6. Direct-phase and observability tests

- [x] 6.1 Update `test_phase_analyze_write_review_finalize.py`: ANALYZE/WRITE/REVIEW advance through phase ports when no per-role Task exists
- [x] 6.2 Test provider failure → IDLE and bounded retry/replay without creating Task/Attempt/Lease records
- [x] 6.3 Test a stale or replayed direct phase result cannot advance a newer state/plan/input fingerprint
- [x] 6.4 Verify StageExecution, phase diagnostics/checkpoints, and Run transition events remain sufficient audit sources after noop Task events disappear
- [x] 6.5 Update deterministic Fake planners and E2E fixtures to produce research-only TaskGraphs
- [x] 6.6 Add a PLAN_APPROVAL/control-surface regression showing the research TaskGraph together with the documented fixed downstream lifecycle

## 7. Verification and documentation

- [x] 7.1 Update README and phase-execution documentation for the research-only Task Runtime, fixed direct-call phases, approval semantics, and event-stream change
- [x] 7.2 Coordinate README edits and archive ordering with `add-orchestration-run-coordinator` and `add-orchestration-llm-ports`
- [x] 7.3 Run focused planner, Replan, RuntimeTick, phase, legacy migration, composition, and E2E tests
- [x] 7.4 Run the full offline `pytest` suite and confirm no real network calls
- [x] 7.5 Run `openspec validate remove-noop-phase-tasks --strict`
- [x] 7.6 Run `gitnexus_detect_changes()` and confirm only the expected symbols and execution flows changed before committing

## 8. Review remediations

- [x] 8.1 Validate all Replan addition roles before any UnitOfWork mutation and regression-test that a caught validation error followed by commit leaves Run, Plan, and Task versions unchanged
- [x] 8.2 Exempt only the fixed Writing deliverable `report.md`; preserve Plan coverage validation for every other CompletionContract path and add a regression test
- [x] 8.3 Run focused and full tests, strict OpenSpec validation, targeted formatting/lint checks, and canonical GitNexus impact/detect checks after the review fixes; record the linked-worktree diff limitation
