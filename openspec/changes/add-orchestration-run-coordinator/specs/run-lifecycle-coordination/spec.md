## ADDED Requirements

### Requirement: Unified create-and-drive entry
The application SHALL expose a public operation that accepts a raw research goal, durably creates a Run, and
drives the complete orchestration lifecycle until the Run is terminal or explicitly blocked.

#### Scenario: Clear goal completes through one entry
- **WHEN** a caller submits a clear goal through the create-and-drive operation using healthy Fake providers
- **THEN** the system normalizes, plans, executes, joins, analyzes, writes, reviews, and finalizes without the caller manually invoking intermediate components

#### Scenario: Create-only request
- **WHEN** a caller explicitly selects create-only behavior
- **THEN** the system persists a CREATED Run and returns without invoking normalization, planning, Workers, or Capabilities

### Requirement: Bounded Run-level advance
The RunCoordinator SHALL advance at most one bounded semantic step per call and SHALL return a structured
disposition of PROGRESSED, BLOCKED, IDLE, or TERMINAL.

#### Scenario: Advance a created Run
- **WHEN** `advance_run` is called for a CREATED Run
- **THEN** the coordinator durably moves it to NORMALIZING and reports PROGRESSED without executing later phases in the same step

#### Scenario: Advance a terminal Run
- **WHEN** `advance_run` is called for a terminal Run
- **THEN** the coordinator performs no work and reports TERMINAL with the stored termination reason

### Requirement: Deterministic phase routing
The coordinator SHALL select phase behavior solely from accepted durable Run state, open Gate state, active
Plan version, and persisted continuation data; model output MUST NOT choose or directly commit a Run state.

#### Scenario: Normalizing phase is selected
- **WHEN** the persisted Run state is NORMALIZING and no Gate is open
- **THEN** only the Goal normalization phase is eligible to prepare or accept work

#### Scenario: Model proposes a later state
- **WHEN** a model response includes an instruction to skip directly to FINALIZING
- **THEN** the coordinator ignores that control instruction and applies only deterministic allowed transitions

### Requirement: Goal-to-plan coordination
The coordinator SHALL persist an accepted GoalSpec and Completion Contract before planning and SHALL validate
and accept a Plan before any Plan Task becomes executable.

#### Scenario: Clear normalized goal
- **WHEN** Goal normalization produces a valid unambiguous outcome bound to the current Run version
- **THEN** the coordinator atomically stores GoalSpec and Completion Contract and transitions the Run to PLANNING

#### Scenario: Materially ambiguous goal
- **WHEN** Goal normalization identifies ambiguity that can materially affect scope, policy, or deliverables
- **THEN** the coordinator opens a GOAL_CLARIFICATION Gate and reports BLOCKED without accepting a Plan

#### Scenario: Invalid plan proposal
- **WHEN** PlanValidator rejects a Plan Proposal
- **THEN** the coordinator stores rejection diagnostics, creates no executable Tasks, and applies bounded retry or failure policy

### Requirement: Phase-aware Task execution
The Task Runtime SHALL execute only Worker roles eligible for the Run's current outer phase and SHALL preserve
existing Attempt, Lease, retry, budget, and late-result acceptance rules.

#### Scenario: Research phase has later-stage Tasks
- **WHEN** the active Plan contains ready EvidenceResearcher and ReportWriter Tasks while the Run is RESEARCHING
- **THEN** only EvidenceResearcher Tasks are eligible for dispatch

#### Scenario: Required phase work remains in flight
- **WHEN** an eligible Task Attempt has not reached an accepted terminal outcome
- **THEN** the coordinator does not transition the Run to the next phase

#### Scenario: Stale phase result arrives
- **WHEN** a result is bound to an older state, plan, contract, lease, or input artifact version
- **THEN** the result is retained as an observation and cannot advance the Run

### Requirement: Durable stage handoffs
The coordinator SHALL pass accepted phase outputs through immutable, content-hashed Artifacts or versioned
domain records and SHALL bind each output to its input versions and hashes.

#### Scenario: Research completes
- **WHEN** all required research Tasks for the active Plan have accepted results
- **THEN** the coordinator deterministically joins them into a persisted EvidenceSet before entering ANALYZING

#### Scenario: Analysis completes
- **WHEN** the Analyst produces a schema-valid result for the current EvidenceSet
- **THEN** the coordinator accepts an immutable AnalysisArtifact bound to the EvidenceSet hash before entering WRITING

#### Scenario: Report draft is accepted
- **WHEN** the ReportWriter produces a valid draft for the current AnalysisArtifact
- **THEN** the coordinator stores a new immutable Report Draft and enters REVIEWING without overwriting older drafts

### Requirement: Idempotent stage execution
The system SHALL durably record prepared and accepted stage executions so restarting or retrying the same
logical stage with the same input fingerprint does not accept duplicate results.

#### Scenario: Process crashes after provider success
- **WHEN** the process restarts after a provider call completed but before its result was durably accepted
- **THEN** the coordinator reconciles the prepared stage execution according to idempotency and recovery policy before issuing another call

#### Scenario: Accepted stage is replayed
- **WHEN** the same logical stage and input fingerprint already has an accepted output
- **THEN** the coordinator reuses the accepted output and does not invoke the provider again

### Requirement: Deterministic review loop
The coordinator SHALL map Reviewer Proposals through deterministic policy and SHALL enforce shared Replan and
report revision bounds before transitioning.

#### Scenario: Reviewer passes the report
- **WHEN** the Reviewer returns PASS and no final review Gate is required
- **THEN** the coordinator transitions the Run to FINALIZING

#### Scenario: Reviewer requests revision within budget
- **WHEN** the Reviewer returns REVISE and report revision budget remains
- **THEN** the coordinator increments the revision counter and returns the Run to WRITING using the current accepted evidence and analysis

#### Scenario: Reviewer identifies a research gap
- **WHEN** the Reviewer returns RESEARCH_GAP and Replan budget remains
- **THEN** the coordinator validates a focused new Plan version, preserves unaffected accepted results, and returns the Run to the allowed research path

#### Scenario: Loop bound is exhausted
- **WHEN** the requested Replan or revision would exceed the effective bound
- **THEN** the coordinator applies the configured partial, failure, or escalation action and does not continue the loop

### Requirement: Persisted Gate continuation
Every Gate opened by the coordinated lifecycle SHALL carry a version-bound continuation describing the
allowed response outcomes and deterministic next action.

#### Scenario: Plan approval is granted
- **WHEN** an authorized response approves a Plan Gate whose bound Plan and state versions are still current
- **THEN** consuming the Gate applies its stored continuation and makes the accepted research phase eligible

#### Scenario: Final review requests changes
- **WHEN** an authorized final review response requests revision
- **THEN** consuming the Gate follows its stored continuation to REVIEWING or WRITING without accepting a caller-supplied arbitrary target state

#### Scenario: Gate continuation is stale
- **WHEN** the Gate's bound state, plan, contract, or artifact version no longer matches the Run
- **THEN** the response is rejected and no continuation action is applied

### Requirement: Pause and resume continuity
Pausing a Run SHALL persist its safe continuation point, and resuming SHALL restore that point without requiring
the caller to choose an arbitrary phase.

#### Scenario: Pause during research
- **WHEN** a pause request is accepted while research work is active
- **THEN** the system stops scheduling new work at a safe boundary and records RESEARCHING as the resumable continuation

#### Scenario: Resume a paused Run
- **WHEN** an authorized resume command targets a PAUSED Run with the current Expected Version
- **THEN** the system restores the persisted continuation and drives until the next block or terminal state

### Requirement: Run Watch uses coordinator progress
Run Watch SHALL loop RunCoordinator advances and MUST NOT classify a Run as blocked only because a Task tick
dispatched zero Tasks.

#### Scenario: Run needs non-Task phase work
- **WHEN** a Run is NORMALIZING or FINALIZING and no Task is ready
- **THEN** Watch continues through the eligible coordinator phase instead of stopping as blocked

#### Scenario: Run has an open Gate
- **WHEN** the coordinator observes a current open Gate
- **THEN** Watch stops and reports BLOCKED with the Gate reason

#### Scenario: Run is temporarily idle
- **WHEN** the coordinator reports IDLE because accepted external work is not yet available
- **THEN** Watch follows configured polling and cancellation behavior without changing formal Run state

### Requirement: Complete composition root
The application composition root SHALL provide explicit Goal, Planner, role-specific Worker, Capability,
Evidence, Gate, Review, Report, Finalizer, Task Runtime, and RunCoordinator dependencies.

#### Scenario: Default offline test composition
- **WHEN** tests build the default offline application
- **THEN** every required port is satisfied by a deterministic Fake and no live network provider is invoked

#### Scenario: Production profile is incomplete
- **WHEN** a production configuration requests a real provider but a required adapter or role handler is missing
- **THEN** application construction fails with a secret-safe diagnostic instead of silently substituting a Fake

### Requirement: Control-surface semantic alignment
The CLI and Python service SHALL share the same coordinator-backed operations for create, start-and-drive,
single advance, watch, Gate continuation, pause, resume, cancel, and inspection.

#### Scenario: CLI starts a Run
- **WHEN** an operator invokes `run start` without `--create-only`
- **THEN** CLI creates and drives the Run until it is terminal or explicitly blocked regardless of whether event following is enabled

#### Scenario: CLI performs one runtime tick
- **WHEN** an operator invokes `runtime tick RUN_ID`
- **THEN** CLI performs one bounded RunCoordinator advance rather than bypassing phase rules with a raw Task-only driver

#### Scenario: Existing Python creation caller
- **WHEN** an existing caller uses the deprecated synchronous creation-compatible `start_run` signature during the compatibility period
- **THEN** the system preserves its creation behavior and emits a documented deprecation path to the canonical operations

### Requirement: Public-boundary end-to-end verification
The project SHALL include end-to-end tests that submit only public commands and verify the complete lifecycle
without directly invoking internal planning, Replan, report, or finalization services.

#### Scenario: Successful research report
- **WHEN** the E2E test submits a clear Goal using deterministic Fake providers
- **THEN** the Run reaches SUCCEEDED and produces hash-valid `report.md`, `report.json`, and `run-summary.json`

#### Scenario: Gate and resume lifecycle
- **WHEN** the E2E test encounters clarification, approval, conflict, or final review policy
- **THEN** it responds through the public Gate API and the coordinator resumes from the stored continuation

#### Scenario: Restart during the lifecycle
- **WHEN** the E2E test restarts the application after any committed semantic checkpoint
- **THEN** the Run continues from durable state without test code manually reconstructing Plan, Tasks, or report objects

### Requirement: Coordinated security boundary
The RunCoordinator and phase handlers MUST preserve the read-only Capability Registry, untrusted-evidence
boundary, secret redaction, policy limits, and deterministic acceptance rules.

#### Scenario: Evidence contains a control instruction
- **WHEN** retrieved or generated evidence instructs the coordinator to invoke an unregistered write capability
- **THEN** the instruction remains untrusted content and cannot alter routing, permissions, or Run state

#### Scenario: Phase failure contains a secret
- **WHEN** an adapter or model failure includes credential-like text
- **THEN** the persisted Event, stage record, CLI diagnostic, and log redact the sensitive value
