## MODIFIED Requirements

### Requirement: Bounded Run-level advance

The RunCoordinator SHALL advance at most one bounded semantic phase step per call and SHALL return a structured disposition of PROGRESSED, BLOCKED, IDLE, or TERMINAL. In RESEARCHING, one advance MAY invoke one RuntimeTick batch, but each selected agent-loop Task MUST advance at most one ResearchStep and invoke at most one Capability.

#### Scenario: Advance a created Run

- **WHEN** `advance_run` is called for a CREATED Run
- **THEN** the coordinator durably moves it to NORMALIZING and reports PROGRESSED without executing later phases in the same step

#### Scenario: Advance an agent-loop research Run

- **WHEN** `advance_run` is called for RESEARCHING with multiple ready agent-loop seed Tasks
- **THEN** one bounded tick advances at most one ResearchStep per selected Task under `max_concurrency`
- **AND** the same coordinator advance does not execute an unbounded inner while-loop

#### Scenario: Advance a terminal Run

- **WHEN** `advance_run` is called for a terminal Run
- **THEN** the coordinator performs no work and reports TERMINAL with the stored termination reason

### Requirement: Phase-aware Task execution

The Task Runtime SHALL schedule Tasks only while the durable Run state is RESEARCHING and SHALL dispatch only `evidence_researcher` Tasks in that state. Every other RunState MUST produce zero Task dispatches. A `fixed_fanout` Task SHALL keep its existing one-attempt behavior; an `agent_loop` Task SHALL remain non-terminal across accepted QUERY/ADD_DIRECTION steps and become terminal only after LoopGuard accepts STOP_REQUEST or deterministic exhaustion. ANALYZE, WRITE and REVIEW SHALL remain coordinator-owned fixed phase ports without Tasks.

#### Scenario: Only research Tasks dispatch through the Task Runtime

- **WHEN** the active Plan contains ready evidence_researcher Tasks and the Run is RESEARCHING
- **THEN** RuntimeTick claims Leases and dispatches according to each persisted Plan execution mode
- **AND** no analyst, report_writer, or report_reviewer Tasks are dispatched

#### Scenario: Successful non-terminal step requeues the Task

- **WHEN** an agent-loop Task accepts QUERY or ADD_DIRECTION without closing the loop
- **THEN** the accepted Attempt/ResearchStep remains auditable
- **AND** the Task returns to a schedulable non-terminal state for the next tick

#### Scenario: Non-research Run state hard-blocks scheduling

- **WHEN** RuntimeTick is invoked while durable Run state is not RESEARCHING and ready Tasks exist
- **THEN** RuntimeTick dispatches zero Tasks and creates no Lease, Attempt or ResearchStep

#### Scenario: Required research work remains active

- **WHEN** any required seed Task has an active or not-yet-closed loop
- **THEN** the coordinator does not transition the Run to ANALYZING

#### Scenario: Stale research step result arrives

- **WHEN** a result is bound to an older state, plan, task, step or lease epoch
- **THEN** the result is retained as an observation and cannot accept Evidence, Direction, usage or advance the Run

### Requirement: Durable stage handoffs

The coordinator SHALL pass accepted phase outputs through immutable, content-hashed Artifacts or versioned domain records and SHALL bind each output to its input versions and hashes. Research SHALL persist every accepted ResearchStep/Evidence increment and SHALL produce one joined EvidenceSet only after all required seed loops close.

#### Scenario: Research step completes

- **WHEN** a current ResearchStep passes fencing and deterministic validation
- **THEN** step state, Evidence, usage, Direction/coverage changes, Event and Checkpoint are atomically accepted

#### Scenario: Research completes

- **WHEN** all required research Tasks for the active Plan have accepted terminal loop outcomes
- **THEN** the coordinator deterministically joins their accepted Evidence into a persisted EvidenceSet before entering ANALYZING

#### Scenario: Analysis completes

- **WHEN** the Analyst and sufficiency funnel accept a schema-valid result for the current EvidenceSet
- **THEN** the coordinator accepts an immutable AnalysisArtifact bound to the EvidenceSet hash before entering WRITING

#### Scenario: Report draft is accepted

- **WHEN** the ReportWriter produces a valid draft for the current AnalysisArtifact
- **THEN** the coordinator stores a new immutable Report Draft and enters REVIEWING without overwriting older drafts
