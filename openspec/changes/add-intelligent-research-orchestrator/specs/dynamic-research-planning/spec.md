## ADDED Requirements

### Requirement: Versioned goal and completion contract
The system SHALL normalize each accepted user goal into a versioned GoalSpec and Completion Contract containing
scope, audience, data policy, deliverables, required criteria, optional criteria, and hard constraints.

#### Scenario: Goal is sufficiently clear
- **WHEN** required goal fields can be safely determined from the request
- **THEN** the system persists GoalSpec and Completion Contract version 1 before planning

#### Scenario: Goal is materially ambiguous
- **WHEN** ambiguity would materially change scope, data policy, budget, or deliverables
- **THEN** the system creates a GOAL_CLARIFICATION Gate instead of silently choosing an interpretation

### Requirement: LLM plan is a proposal
The Planner SHALL output a schema-valid PlanGraph Proposal and MUST NOT create Tasks or invoke Capabilities
directly.

#### Scenario: Planner returns a plan
- **WHEN** the model produces a PlanGraph Proposal
- **THEN** the system submits it to PlanValidator before any Task is materialized

#### Scenario: Planner requests an unregistered capability
- **WHEN** a Plan Proposal names a Worker or Capability absent from the Registry
- **THEN** PlanValidator rejects the Plan without creating Tasks

### Requirement: Deterministic plan validation
PlanValidator SHALL validate DAG structure, Task input/output contracts, registered Worker and Capability,
required deliverable production paths, policy, budget, Task count, graph depth, and concurrency bounds.

#### Scenario: Plan contains a cycle
- **WHEN** dependency validation finds a cycle
- **THEN** the Plan is rejected with a structured validation error

#### Scenario: Required deliverable has no producer
- **WHEN** no Task in the Plan can produce a required Completion Contract deliverable
- **THEN** the Plan is rejected before execution

### Requirement: Bounded plan graph
The default system policy SHALL limit a Run to 32 Tasks, depth 4, concurrency 4, 2 Replans, 2 report revisions,
3 Attempts per Task, and a 30-minute Run Deadline. Run Policy MUST NOT exceed system maximums.

#### Scenario: Proposal exceeds task limit
- **WHEN** a Plan Proposal contains more Tasks than the effective maximum
- **THEN** PlanValidator rejects it or requests a smaller Plan without partially materializing Tasks

#### Scenario: Run policy attempts to expand a system maximum
- **WHEN** user-supplied Run Policy exceeds a configured system maximum
- **THEN** the request is rejected or clamped according to explicit application policy and the effective value is reported

### Requirement: Versioned replan
Each accepted Replan SHALL create a new Plan Version, preserve unaffected accepted results, and mark invalidated
old Tasks as SUPERSEDED rather than rewriting history.

#### Scenario: Evidence gap triggers focused replan
- **WHEN** review identifies a required evidence gap and Replan budget remains
- **THEN** the system validates and commits a new Plan Version containing focused additional Tasks

#### Scenario: Completed task remains valid
- **WHEN** a Replan does not change a completed Task input or dependencies
- **THEN** the accepted result is retained and the Task is not executed again

#### Scenario: Old task returns after replan
- **WHEN** a SUPERSEDED Task Attempt returns a late result
- **THEN** the result cannot update the current Plan State

### Requirement: Completion contract amendment
Changes to GoalSpec or Completion Contract SHALL require an authorized, versioned amendment with actor, reason,
and invalidated validation results.

#### Scenario: User narrows the goal
- **WHEN** an authorized Gate response removes a required publication deliverable
- **THEN** a new Completion Contract version is stored and affected Plan and completion validations are recalculated

#### Scenario: Agent tries to lower completion criteria
- **WHEN** an Agent Proposal removes a required criterion without authorized amendment
- **THEN** the system rejects the change

### Requirement: Structured termination proposal
The planning and review system SHALL produce structured Continue, Replan, Revise, Pause, Escalate, Degrade, or
Terminate Proposals. The deterministic TerminationGuard SHALL make the formal decision.

#### Scenario: Model claims completion prematurely
- **WHEN** a model proposes completion while a required criterion is unsatisfied or unknown
- **THEN** TerminationGuard rejects successful completion and selects an allowed control action
