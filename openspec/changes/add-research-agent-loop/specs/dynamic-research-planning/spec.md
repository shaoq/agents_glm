## MODIFIED Requirements

### Requirement: LLM plan is a proposal

The Planner SHALL output a schema-valid research-only PlanGraph Proposal and MUST NOT create Tasks, Directions, ResearchSteps, or invoke Capabilities directly. An `agent_loop` proposal SHALL include at least one `EVIDENCE_RESEARCHER` seed Task plus a versioned ExplorationBoundary; a `fixed_fanout` proposal SHALL retain the existing Task/source semantics.

#### Scenario: Planner returns an agent-loop plan

- **WHEN** the model produces an agent-loop PlanGraph Proposal
- **THEN** the system submits seed Tasks, execution mode and ExplorationBoundary to PlanValidator before any Task, Loop or Step is materialized

#### Scenario: Planner requests an unregistered capability

- **WHEN** a Plan Proposal names a Worker or Capability absent from the Registry or outside the effective policy
- **THEN** PlanValidator rejects the Plan without creating Tasks, Loops, Directions or Steps

#### Scenario: Planner emits no seed

- **WHEN** an agent-loop Plan Proposal contains no seed Task
- **THEN** PlanValidator rejects the Proposal rather than allowing the Agent to invent an unapproved initial scope

### Requirement: Deterministic plan validation

PlanValidator SHALL validate DAG structure, research-only Task role, Task input/output contracts, registered Worker and Capability, required deliverable production paths, policy, Task count, graph depth, concurrency bounds, research execution mode and ExplorationBoundary. For agent-loop Plans it MUST additionally validate non-empty seeds, allowed capabilities, per-seed required coverage subset, per-seed `max_steps`, `max_directions` and loop budget ceiling before the first write; for finite Run budgets, the sum of all seed worst-case ceilings MUST fit the available Run budget.

#### Scenario: Plan contains a cycle

- **WHEN** dependency validation finds a cycle
- **THEN** the Plan is rejected with a structured validation error

#### Scenario: Required deliverable has no producer

- **WHEN** no Task or fixed lifecycle phase in the Plan contract can produce a required Completion Contract deliverable
- **THEN** the Plan is rejected before execution

#### Scenario: Exploration boundary is invalid

- **WHEN** required coverage is outside the allowed capability set or any exploration limit exceeds system/Run Policy
- **THEN** the Plan is rejected before Plan, Task, Gate, Run or Event mutation

### Requirement: Bounded plan graph

The default system policy SHALL limit a Run to 32 Tasks, depth 4, concurrency 4, 2 Replans, 2 report revisions, 3 failure Attempts per logical Task/ResearchStep, a 30-minute Run Deadline, and configured hard maxima for per-seed agent-loop steps and directions. Run Policy and ExplorationBoundary MUST NOT exceed system maximums; each seed loop ceiling and their finite worst-case sum MUST remain within the shared Run budget.

#### Scenario: Proposal exceeds task limit

- **WHEN** a Plan Proposal contains more Tasks than the effective maximum
- **THEN** PlanValidator rejects it or requests a smaller Plan without partially materializing Tasks

#### Scenario: Loop boundary exceeds system maximum

- **WHEN** an agent-loop Plan requests more steps, directions, tokens or cost than the effective maximum
- **THEN** PlanValidator rejects it or requests a smaller boundary without partially materializing state

#### Scenario: Run policy attempts to expand a system maximum

- **WHEN** user-supplied Run Policy exceeds a configured system maximum
- **THEN** the request is rejected or clamped according to explicit application policy and the effective value is reported

### Requirement: Versioned replan

Each accepted formal Replan SHALL create a new Plan Version, preserve unaffected accepted results, and mark invalidated old Tasks as SUPERSEDED rather than rewriting history. A loop-local ADD_DIRECTION within the current ExplorationBoundary MUST NOT be classified as a Replan and MUST NOT change Plan Version or `replan_count`.

#### Scenario: Evidence gap triggers focused replan

- **WHEN** ANALYZE or REVIEW identifies a required evidence gap outside completed loop work and Replan budget remains
- **THEN** the system validates and commits a new Plan Version containing focused additional seed Tasks/boundary

#### Scenario: Loop adds an in-boundary direction

- **WHEN** Research Agent adds a sanitized Direction within the accepted Plan boundary
- **THEN** the Direction is stored under the current Plan/Task loop
- **AND** no Plan version or Replan counter changes

#### Scenario: Completed task remains valid

- **WHEN** a formal Replan does not change a completed Task input, boundary or dependencies
- **THEN** the accepted result is retained and the Task is not executed again

#### Scenario: Old task returns after replan

- **WHEN** a SUPERSEDED Task Attempt or ResearchStep returns a late result
- **THEN** the result cannot update the current Plan State, Evidence, Direction or budget
