## 1. Baseline and Architecture Contracts

- [x] 1.1 Record the current public CLI and OrchestrationService behavior in characterization tests
- [x] 1.2 Add a failing public-boundary test proving a clear Goal currently remains in NORMALIZING
- [x] 1.3 Add architecture tests for the Application → RunCoordinator → Task Runtime dependency direction
- [x] 1.4 Add architecture tests preventing CLI and phase handlers from importing SQLite implementations directly
- [x] 1.5 Add architecture tests preventing sibling projects from depending on agents_orchestration
- [x] 1.6 Run GitNexus impact analysis for every existing symbol selected for modification and record risk

## 2. Coordination Domain Contracts

- [x] 2.1 Add tests for AdvanceDisposition values PROGRESSED, BLOCKED, IDLE, and TERMINAL
- [x] 2.2 Implement immutable AdvanceReport with Run, state, version, reason, and optional Task tick data
- [x] 2.3 Add tests for deterministic RunState-to-phase routing
- [x] 2.4 Implement phase identifiers and the fixed phase routing table
- [x] 2.5 Add tests for logical stage keys and input fingerprints
- [x] 2.6 Implement StageExecution status and version/hash binding models
- [x] 2.7 Add tests for phase result acceptance against state, plan, contract, and artifact versions
- [x] 2.8 Implement deterministic phase acceptance and stale-result classification
- [x] 2.9 Add tests proving model or evidence content cannot select a formal next Run state

## 3. Stage Execution Persistence

- [ ] 3.1 Add migration tests for StageExecution and continuation schema on a fresh SQLite database
- [ ] 3.2 Add migration tests upgrading an existing orchestration database without deleting Run history
- [ ] 3.3 Add additive SQLite schema for stage executions, input fingerprints, output refs, and statuses
- [ ] 3.4 Implement StageExecution repository create, lookup, accept, reject, fail, and supersede operations
- [ ] 3.5 Enforce one accepted StageExecution per Run, logical stage key, and input fingerprint
- [ ] 3.6 Add repository tests for idempotent prepare and accepted-result reuse
- [ ] 3.7 Add compare-and-set tests for concurrent stage acceptance
- [ ] 3.8 Integrate the StageExecution repository into UnitOfWork and persistence ports
- [ ] 3.9 Add serialization round-trip tests for stage records and immutable Artifact refs

## 4. RunCoordinator Core

- [ ] 4.1 Add a failing test for CREATED → NORMALIZING as one bounded coordinator advance
- [ ] 4.2 Implement the RunCoordinator dispatcher with injected phase handlers
- [ ] 4.3 Implement CREATED, terminal, paused, and open-Gate short-circuit behavior
- [ ] 4.4 Add tests proving one advance cannot execute more than one semantic phase
- [ ] 4.5 Implement prepare/execute/accept orchestration without provider calls in write transactions
- [ ] 4.6 Add tests for stale phase results becoming observations without advancing the Run
- [ ] 4.7 Implement stage Event and semantic Checkpoint creation in the acceptance transaction
- [ ] 4.8 Add tests distinguishing IDLE from BLOCKED when no Task is dispatched
- [ ] 4.9 Apply deadline, token, cost, Replan, revision, and cancellation guards before every phase step
- [ ] 4.10 Add structured, redacted coordinator diagnostics for validation, policy, recovery, and terminal errors

## 5. Goal and Planning Phases

- [ ] 5.1 Define async GoalNormalizer and Planner composition contracts used by phase handlers
- [ ] 5.2 Add Goal phase tests for clear, ambiguous, invalid, stale, and provider-failure outcomes
- [ ] 5.3 Implement Goal phase snapshot, external normalization, deterministic validation, and atomic acceptance
- [ ] 5.4 Persist accepted GoalSpec and Completion Contract before entering PLANNING
- [ ] 5.5 Open a version-bound GOAL_CLARIFICATION Gate for material ambiguity
- [ ] 5.6 Add Planning phase tests for valid, invalid, incomplete-role, approval-required, and stale proposals
- [ ] 5.7 Extend Plan validation to require phase-role and deliverable coverage
- [ ] 5.8 Implement Planning phase proposal, validation, rejection policy, and Plan acceptance
- [ ] 5.9 Open PLAN_APPROVAL before Tasks become executable when policy requires approval
- [ ] 5.10 Add tests proving a rejected or unapproved Plan materializes no executable Tasks

## 6. Phase-aware Task Runtime and Research Join

- [ ] 6.1 Add scheduler tests mapping Run phases to eligible Worker roles
- [ ] 6.2 Implement phase-role filtering for Task readiness and dispatch
- [ ] 6.3 Preserve existing Attempt, Lease, retry, fencing, budget, and late-result behavior under filtering
- [ ] 6.4 Add tests proving later-stage Tasks cannot dispatch during RESEARCHING
- [ ] 6.5 Add tests for required phase Tasks succeeded, failed, in flight, skipped, and superseded
- [ ] 6.6 Implement phase completion detection for the active Plan version
- [ ] 6.7 Add Research phase tests for independent branch success, partial failure, conflict, and insufficient evidence
- [ ] 6.8 Implement accepted research result loading and deterministic Evidence Join
- [ ] 6.9 Persist the EvidenceSet as an immutable stage output bound to active Plan and Attempt results
- [ ] 6.10 Implement Research outcomes for ANALYZING, focused Replan, conflict Gate, degradation, and failure
- [ ] 6.11 Add tests proving Replan preserves unaffected accepted results and rejects late superseded results

## 7. Analyze, Write, Review, and Finalize Phases

- [ ] 7.1 Implement role-specific Analyst, ReportWriter, and ReportReviewer handler contracts
- [ ] 7.2 Add Analysis phase tests for evidence binding, unsupported claims, retry, and focused research fallback
- [ ] 7.3 Implement Analysis phase acceptance and immutable AnalysisArtifact persistence
- [ ] 7.4 Add Writing phase tests for current analysis binding, citation structure, and immutable draft revision
- [ ] 7.5 Implement Writing phase acceptance and Report Draft artifact persistence
- [ ] 7.6 Add Review phase tests for PASS, REVISE, RESEARCH_GAP, CONFLICT, and ESCALATE
- [ ] 7.7 Implement deterministic Reviewer verdict mapping through Run Policy
- [ ] 7.8 Implement monotonic revision and Replan counters without reset across phase transitions
- [ ] 7.9 Add tests for exhausted revision/Replan bounds selecting partial, fail, or escalation policy
- [ ] 7.10 Add Finalizing tests for completion, citations, required Tasks, conflicts, pending Attempts, and stale candidate versions
- [ ] 7.11 Implement Finalizing phase using CompletionEvaluator, CitationValidator, ReportBuilder, and Finalizer
- [ ] 7.12 Atomically bind final artifact hashes, final Checkpoint, termination reason, and terminal Run state
- [ ] 7.13 Verify partial delivery discloses unmet criteria, missing sources, unresolved conflicts, and degradation

## 8. Gate, Pause, and Continuation

- [ ] 8.1 Add GateContinuation domain tests for origin phase, bound versions, outcomes, actions, and artifact hashes
- [ ] 8.2 Extend Gate persistence and serialization with versioned continuation data
- [ ] 8.3 Implement atomic Gate open plus continuation, Event, Outbox, and Checkpoint
- [ ] 8.4 Add Gate response tests rejecting stale state, plan, contract, and artifact bindings
- [ ] 8.5 Implement deterministic continuation application during Gate consumption
- [ ] 8.6 Implement GOAL_CLARIFICATION, PLAN_APPROVAL, CONFLICT_RESOLUTION, and FINAL_REVIEW continuations
- [ ] 8.7 Add tests proving callers cannot supply an arbitrary post-Gate target state
- [ ] 8.8 Add Pause continuation tests for every resumable outer phase
- [ ] 8.9 Persist the safe phase continuation when Pause is accepted
- [ ] 8.10 Replace arbitrary resume target selection with persisted continuation restoration
- [ ] 8.11 Implement resume-and-drive behavior until the next explicit block or terminal state
- [ ] 8.12 Add Gate expiry tests for fail, cancel, partial, default, and escalate actions

## 9. Composition Root and Provider Wiring

- [ ] 9.1 Define a composition configuration that distinguishes deterministic Fake and production profiles
- [ ] 9.2 Build the deterministic offline composition with all required phase ports and role handlers
- [ ] 9.3 Add composition tests proving the offline profile imports and calls no live network stack
- [ ] 9.4 Build production composition for Model, Memory, RAG, and optional Web adapters
- [ ] 9.5 Wire GoalNormalizer and Planner to configured model profiles through proposal-only ports
- [ ] 9.6 Wire EvidenceResearcher, Analyst, ReportWriter, and ReportReviewer to role-specific handlers
- [ ] 9.7 Wire Plan, Replan, Evidence Join, Gate, Completion, Report, Finalizer, Task Runtime, and RunCoordinator
- [ ] 9.8 Reject incomplete production composition instead of silently falling back to Fake providers
- [ ] 9.9 Add secret-safe capability and composition diagnostics
- [ ] 9.10 Verify all Capability calls still pass through Worker permissions, Run Policy, and Capability Router

## 10. Application Service, CLI, and Watch

- [ ] 10.1 Add application tests for create_run, start_and_drive, advance_run, drive_run, and resume_and_drive
- [ ] 10.2 Implement typed StartRunCommand and structured start/drive results
- [ ] 10.3 Implement create_run as idempotent CREATED-only persistence
- [ ] 10.4 Implement start_and_drive over create_run plus coordinator-backed drive_run
- [ ] 10.5 Preserve the legacy synchronous start_run creation behavior with a documented deprecation warning
- [ ] 10.6 Change RuntimeWatch to loop RunCoordinator advances and honor PROGRESSED/BLOCKED/IDLE/TERMINAL
- [ ] 10.7 Add Watch tests for non-Task phases, open Gates, idle polling, cancellation, maximum advances, and terminal Runs
- [ ] 10.8 Route `run start` to start_and_drive and `--create-only` to create_run
- [ ] 10.9 Make `--follow` control event presentation without changing execution semantics
- [ ] 10.10 Route `runtime tick RUN_ID` to one bounded RunCoordinator advance
- [ ] 10.11 Route selected and global `runtime watch` through coordinator-backed Watch
- [ ] 10.12 Remove CLI arbitrary `run resume --target` and use resume_and_drive continuation semantics
- [ ] 10.13 Return the freshly loaded final or blocked Run view rather than the pre-drive creation snapshot
- [ ] 10.14 Add stable JSON and human-readable output for AdvanceReport, Gate block, and terminal results

## 11. Recovery and Compatibility

- [ ] 11.1 Define recovery rules for each Run phase and StageExecution status
- [ ] 11.2 Add restart tests before prepare, after prepare, after provider return, after acceptance, and after transition
- [ ] 11.3 Implement accepted stage result reuse by logical key and input fingerprint
- [ ] 11.4 Implement recovery handling for PREPARED executions with unknown external outcomes
- [ ] 11.5 Reconstruct lazy stage records for legacy non-terminal Runs from Run, Plan, Task, Gate, and Artifact state
- [ ] 11.6 Add compatibility tests for legacy databases and the deprecated Python start_run signature
- [ ] 11.7 Add tests proving restart never duplicates accepted final artifacts or Task results
- [ ] 11.8 Verify global Watch resumes every eligible Run through the coordinator without changing paused or gated Runs
- [ ] 11.9 Document rollback routing and verify additive migrations do not prevent the prior binary from reading core Run data

## 12. Public-boundary E2E, Security, and Documentation

- [ ] 12.1 Replace the manual happy-path E2E with a public start_and_drive Goal-to-artifacts test
- [ ] 12.2 Assert the happy-path E2E directly invokes no PlanAcceptor, ReplanService, ReportBuilder, or Finalizer
- [ ] 12.3 Add public-boundary E2E for all four Gate types and stored continuation
- [ ] 12.4 Add public-boundary E2E for focused Replan and report revision
- [ ] 12.5 Add public-boundary E2E for Pause/Resume and process restart at semantic checkpoints
- [ ] 12.6 Add public-boundary E2E for provider degradation, deadline, budget, cancellation, and partial/failure delivery
- [ ] 12.7 Verify `report.md`, `report.json`, and `run-summary.json` content hashes and disclosure fields
- [ ] 12.8 Add security tests for prompt injection, unregistered write capabilities, secret redaction, and export boundaries
- [ ] 12.9 Update README execution semantics, Python API examples, Runtime commands, Gate resume, and recovery guidance
- [ ] 12.10 Update the architecture design status and diagrams to include RunCoordinator above TaskRuntimeTick
- [ ] 12.11 Run Ruff, unit, integration, architecture, contract, E2E, and coverage suites
- [ ] 12.12 Run strict OpenSpec validation and implementation consistency verification
- [ ] 12.13 Run GitNexus detect-changes before commit and confirm only intended symbols and flows changed
