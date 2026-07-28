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

- [x] 3.1 Add migration tests for StageExecution and continuation schema on a fresh SQLite database
- [x] 3.2 Add migration tests upgrading an existing orchestration database without deleting Run history
- [x] 3.3 Add additive SQLite schema for stage executions, input fingerprints, output refs, and statuses
- [x] 3.4 Implement StageExecution repository create, lookup, accept, reject, fail, and supersede operations
- [x] 3.5 Enforce one accepted StageExecution per Run, logical stage key, and input fingerprint
- [x] 3.6 Add repository tests for idempotent prepare and accepted-result reuse
- [x] 3.7 Add compare-and-set tests for concurrent stage acceptance
- [x] 3.8 Integrate the StageExecution repository into UnitOfWork and persistence ports
- [x] 3.9 Add serialization round-trip tests for stage records and immutable Artifact refs

## 4. RunCoordinator Core

- [x] 4.1 Add a failing test for CREATED → NORMALIZING as one bounded coordinator advance
- [x] 4.2 Implement the RunCoordinator dispatcher with injected phase handlers
- [x] 4.3 Implement CREATED, terminal, paused, and open-Gate short-circuit behavior
- [x] 4.4 Add tests proving one advance cannot execute more than one semantic phase
- [x] 4.5 Implement prepare/execute/accept orchestration without provider calls in write transactions
- [x] 4.6 Add tests for stale phase results becoming observations without advancing the Run
- [x] 4.7 Implement stage Event and semantic Checkpoint creation in the acceptance transaction
- [x] 4.8 Add tests distinguishing IDLE from BLOCKED when no Task is dispatched
- [x] 4.9 Apply deadline, token, cost, Replan, revision, and cancellation guards before every phase step
- [x] 4.10 Add structured, redacted coordinator diagnostics for validation, policy, recovery, and terminal errors

## 5. Goal and Planning Phases

- [x] 5.1 Define async GoalNormalizer and Planner composition contracts used by phase handlers
- [x] 5.2 Add Goal phase tests for clear, ambiguous, invalid, stale, and provider-failure outcomes
- [x] 5.3 Implement Goal phase snapshot, external normalization, deterministic validation, and atomic acceptance
- [x] 5.4 Persist accepted GoalSpec and Completion Contract before entering PLANNING
- [x] 5.5 Open a version-bound GOAL_CLARIFICATION Gate for material ambiguity
- [x] 5.6 Add Planning phase tests for valid, invalid, incomplete-role, approval-required, and stale proposals
- [x] 5.7 Extend Plan validation to require phase-role and deliverable coverage
- [x] 5.8 Implement Planning phase proposal, validation, rejection policy, and Plan acceptance
- [x] 5.9 Open PLAN_APPROVAL before Tasks become executable when policy requires approval
- [x] 5.10 Add tests proving a rejected or unapproved Plan materializes no executable Tasks

## 6. Phase-aware Task Runtime and Research Join

- [x] 6.1 Add scheduler tests mapping Run phases to eligible Worker roles
- [x] 6.2 Implement phase-role filtering for Task readiness and dispatch
- [x] 6.3 Preserve existing Attempt, Lease, retry, fencing, budget, and late-result behavior under filtering
- [x] 6.4 Add tests proving later-stage Tasks cannot dispatch during RESEARCHING
- [x] 6.5 Add tests for required phase Tasks succeeded, failed, in flight, skipped, and superseded
- [x] 6.6 Implement phase completion detection for the active Plan version
- [x] 6.7 Add Research phase tests for independent branch success, partial failure, conflict, and insufficient evidence
- [x] 6.8 Implement accepted research result loading and deterministic Evidence Join
- [x] 6.9 Persist the EvidenceSet as an immutable stage output bound to active Plan and Attempt results
- [x] 6.10 Implement Research outcomes for ANALYZING, focused Replan, conflict Gate, degradation, and failure
- [ ] 6.11 Add tests proving Replan preserves unaffected accepted results and rejects late superseded results

## 7. Analyze, Write, Review, and Finalize Phases

- [x] 7.1 Implement role-specific Analyst, ReportWriter, and ReportReviewer handler contracts
- [x] 7.2 Add Analysis phase tests for evidence binding, unsupported claims, retry, and focused research fallback
- [x] 7.3 Implement Analysis phase acceptance and immutable AnalysisArtifact persistence
- [x] 7.4 Add Writing phase tests for current analysis binding, citation structure, and immutable draft revision
- [x] 7.5 Implement Writing phase acceptance and Report Draft artifact persistence
- [x] 7.6 Add Review phase tests for PASS, REVISE, RESEARCH_GAP, CONFLICT, and ESCALATE
- [x] 7.7 Implement deterministic Reviewer verdict mapping through Run Policy
- [x] 7.8 Implement monotonic revision and Replan counters without reset across phase transitions
- [x] 7.9 Add tests for exhausted revision/Replan bounds selecting partial, fail, or escalation policy
- [x] 7.10 Add Finalizing tests for completion, citations, required Tasks, conflicts, pending Attempts, and stale candidate versions
- [x] 7.11 Implement Finalizing phase using CompletionEvaluator, CitationValidator, ReportBuilder, and Finalizer
- [x] 7.12 Atomically bind final artifact hashes, final Checkpoint, termination reason, and terminal Run state
- [x] 7.13 Verify partial delivery discloses unmet criteria, missing sources, unresolved conflicts, and degradation

## 8. Gate, Pause, and Continuation

- [x] 8.1 Add GateContinuation domain tests for origin phase, bound versions, outcomes, actions, and artifact hashes
- [x] 8.2 Extend Gate persistence and serialization with versioned continuation data
- [x] 8.3 Implement atomic Gate open plus continuation, Event, Outbox, and Checkpoint
- [x] 8.4 Add Gate response tests rejecting stale state, plan, contract, and artifact bindings
- [x] 8.5 Implement deterministic continuation application during Gate consumption
- [x] 8.6 Implement GOAL_CLARIFICATION, PLAN_APPROVAL, CONFLICT_RESOLUTION, and FINAL_REVIEW continuations
- [x] 8.7 Add tests proving callers cannot supply an arbitrary post-Gate target state
- [x] 8.8 Add Pause continuation tests for every resumable outer phase
- [x] 8.9 Persist the safe phase continuation when Pause is accepted
- [x] 8.10 Replace arbitrary resume target selection with persisted continuation restoration
- [x] 8.11 Implement resume-and-drive behavior until the next explicit block or terminal state
- [x] 8.12 Add Gate expiry tests for fail, cancel, partial, default, and escalate actions

## 9. Composition Root and Provider Wiring

- [x] 9.1 Define a composition configuration that distinguishes deterministic Fake and production profiles
- [x] 9.2 Build the deterministic offline composition with all required phase ports and role handlers
- [x] 9.3 Add composition tests proving the offline profile imports and calls no live network stack
- [ ] 9.4 Build production composition for Model, Memory, RAG, and optional Web adapters
- [ ] 9.5 Wire GoalNormalizer and Planner to configured model profiles through proposal-only ports
- [x] 9.6 Wire EvidenceResearcher, Analyst, ReportWriter, and ReportReviewer to role-specific handlers
- [x] 9.7 Wire Plan, Replan, Evidence Join, Gate, Completion, Report, Finalizer, Task Runtime, and RunCoordinator
- [ ] 9.8 Reject incomplete production composition instead of silently falling back to Fake providers
- [ ] 9.9 Add secret-safe capability and composition diagnostics
- [x] 9.10 Verify all Capability calls still pass through Worker permissions, Run Policy, and Capability Router

## 10. Application Service, CLI, and Watch

- [x] 10.1 Add application tests for create_run, start_and_drive, advance_run, drive_run, and resume_and_drive
- [ ] 10.2 Implement typed StartRunCommand and structured start/drive results
- [x] 10.3 Implement create_run as idempotent CREATED-only persistence
- [x] 10.4 Implement start_and_drive over create_run plus coordinator-backed drive_run
- [ ] 10.5 Preserve the legacy synchronous start_run creation behavior with a documented deprecation warning
- [x] 10.6 Change RuntimeWatch to loop RunCoordinator advances and honor PROGRESSED/BLOCKED/IDLE/TERMINAL
- [ ] 10.7 Add Watch tests for non-Task phases, open Gates, idle polling, cancellation, maximum advances, and terminal Runs
- [x] 10.8 Route `run start` to start_and_drive and `--create-only` to create_run
- [ ] 10.9 Make `--follow` control event presentation without changing execution semantics
- [x] 10.10 Route `runtime tick RUN_ID` to one bounded RunCoordinator advance
- [x] 10.11 Route selected and global `runtime watch` through coordinator-backed Watch
- [x] 10.12 Remove CLI arbitrary `run resume --target` and use resume_and_drive continuation semantics
- [x] 10.13 Return the freshly loaded final or blocked Run view rather than the pre-drive creation snapshot
- [x] 10.14 Add stable JSON and human-readable output for AdvanceReport, Gate block, and terminal results

## 11. Recovery and Compatibility

- [ ] 11.1 Define recovery rules for each Run phase and StageExecution status
- [x] 11.2 Add restart tests before prepare, after prepare, after provider return, after acceptance, and after transition
- [x] 11.3 Implement accepted stage result reuse by logical key and input fingerprint
- [ ] 11.4 Implement recovery handling for PREPARED executions with unknown external outcomes
- [ ] 11.5 Reconstruct lazy stage records for legacy non-terminal Runs from Run, Plan, Task, Gate, and Artifact state
- [x] 11.6 Add compatibility tests for legacy databases and the deprecated Python start_run signature
- [x] 11.7 Add tests proving restart never duplicates accepted final artifacts or Task results
- [x] 11.8 Verify global Watch resumes every eligible Run through the coordinator without changing paused or gated Runs
- [x] 11.9 Document rollback routing and verify additive migrations do not prevent the prior binary from reading core Run data

## 12. Public-boundary E2E, Security, and Documentation

- [x] 12.1 Replace the manual happy-path E2E with a public start_and_drive Goal-to-artifacts test
- [x] 12.2 Assert the happy-path E2E directly invokes no PlanAcceptor, ReplanService, ReportBuilder, or Finalizer
- [ ] 12.3 Add public-boundary E2E for all four Gate types and stored continuation
- [ ] 12.4 Add public-boundary E2E for focused Replan and report revision
- [x] 12.5 Add public-boundary E2E for Pause/Resume and process restart at semantic checkpoints
- [ ] 12.6 Add public-boundary E2E for provider degradation, deadline, budget, cancellation, and partial/failure delivery
- [x] 12.7 Verify `report.md`, `report.json`, and `run-summary.json` content hashes and disclosure fields
- [ ] 12.8 Add security tests for prompt injection, unregistered write capabilities, secret redaction, and export boundaries
- [x] 12.9 Update README execution semantics, Python API examples, Runtime commands, Gate resume, and recovery guidance
- [x] 12.10 Update the architecture design status and diagrams to include RunCoordinator above TaskRuntimeTick
- [x] 12.11 Run Ruff, unit, integration, architecture, contract, E2E, and coverage suites
- [x] 12.12 Run strict OpenSpec validation and implementation consistency verification
- [x] 12.13 Run GitNexus detect-changes before commit and confirm only intended symbols and flows changed
