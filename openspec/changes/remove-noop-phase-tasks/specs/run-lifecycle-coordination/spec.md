## MODIFIED Requirements

### Requirement: Phase-aware Task execution
The Task Runtime SHALL schedule Tasks only while the durable Run state is RESEARCHING and SHALL dispatch only evidence_researcher Tasks in that state. Every other RunState MUST produce zero Task dispatches, even when ready or legacy Tasks exist. New Plan and Replan acceptance MUST reject analyst, report_writer, and report_reviewer TaskSpecs. The ANALYZE, WRITE, and REVIEW phases SHALL be executed by their phase handlers calling coordinator-owned model-backed phase ports directly, without a Task, Lease, or Attempt for that phase call. Existing Attempt, Lease, retry, budget, fencing, and late-result acceptance rules SHALL remain unchanged for research Tasks. Phase ordering SHALL be enforced by accepted durable Run transitions and current input fingerprints.

#### Scenario: Only research Tasks dispatch through the Task Runtime
- **WHEN** the active Plan contains ready evidence_researcher Tasks
- **AND** the durable Run state is RESEARCHING
- **THEN** the Task Runtime claims Leases, creates Attempts, and dispatches them subject to phase-role eligibility
- **AND** no analyst, report_writer, or report_reviewer Tasks exist in the Plan

#### Scenario: Non-research Run state hard-blocks scheduling
- **WHEN** RuntimeTick is invoked while the durable Run state is not RESEARCHING
- **AND** one or more ready Tasks exist
- **THEN** RuntimeTick dispatches zero Tasks
- **AND** it creates no Lease or Attempt

#### Scenario: Non-research Task role is proposed
- **WHEN** a new Plan Proposal or Replan Proposal contains an analyst, report_writer, or report_reviewer TaskSpec
- **THEN** deterministic validation rejects the proposal
- **AND** no executable Task for that role is materialized

#### Scenario: Plan approval presents dynamic and fixed work separately
- **WHEN** PLAN_APPROVAL presents a research-only PlanGraph
- **THEN** the control surface identifies that graph as the dynamic research dispatch plan
- **AND** it identifies ANALYZE, WRITE, REVIEW, and FINALIZE separately as fixed downstream lifecycle phases
- **AND** it does not create synthetic Tasks for lifecycle visibility

#### Scenario: Final report is a lifecycle-owned deliverable
- **WHEN** the Completion Contract requires report.md
- **THEN** deterministic Plan validation recognizes the fixed Writing phase as the owner of that final deliverable
- **AND** it does not require an evidence_researcher Task to claim report.md as its deliverable

#### Scenario: Analysis, writing, and review execute via phase ports
- **WHEN** the Run enters ANALYZING, WRITING, or REVIEWING
- **THEN** the phase handler invokes its coordinator-owned model port directly without depending on a per-role Task
- **AND** no Task is dispatched and no Lease or Attempt is created for that role

#### Scenario: Prerequisite gating is preserved by the state machine
- **WHEN** the Run enters ANALYZING
- **THEN** the accepted durable Run transition proves required research Tasks succeeded and the evidence join validated
- **AND** the analyst port reloads accepted evidence records without a Task gate
- **AND** this requirement does not imply that the joined EvidenceSet or later phase outputs are newly persisted by this change

#### Scenario: Legacy no-op Tasks exist
- **WHEN** an existing active Plan contains analyst, report_writer, or report_reviewer Tasks after upgrade
- **THEN** existing terminal history remains available for audit
- **AND** PENDING or READY legacy Tasks transition to SKIPPED without dispatch
- **AND** DISPATCHED or AWAITING_RETRY legacy Tasks transition to CANCELED
- **AND** active leases for canceled legacy Tasks are invalidated so late results cannot advance the Run
- **AND** a subsequent Replan does not carry those Tasks into the new Plan version

#### Scenario: Required research work remains in flight
- **WHEN** an eligible research Task Attempt has not reached an accepted terminal outcome
- **THEN** the coordinator does not transition the Run to ANALYZING

#### Scenario: Stale phase result arrives
- **WHEN** a result is bound to an older state, plan, contract, lease, or input artifact version
- **THEN** the result is retained as an observation and cannot advance the Run
