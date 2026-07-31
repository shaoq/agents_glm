## MODIFIED Requirements

### Requirement: Supported human gate types

The system SHALL support GOAL_CLARIFICATION, PLAN_APPROVAL, CONFLICT_RESOLUTION, and FINAL_REVIEW Gate types, each with a typed request and response schema. PLAN_APPROVAL for an agent-loop Plan MUST present the non-empty seed Tasks, research execution mode, allowed capabilities, and each seed's required coverage, `max_steps`, `max_directions`, and loop budget ceiling separately from fixed downstream lifecycle phases.

#### Scenario: Ambiguous goal requires clarification

- **WHEN** GoalNormalizer cannot safely resolve a material ambiguity
- **THEN** it creates a GOAL_CLARIFICATION Gate with explicit questions and allowed response fields

#### Scenario: Agent-loop plan requires approval

- **WHEN** policy requires PLAN_APPROVAL for an agent-loop Plan
- **THEN** the Gate request displays and binds the exact seed and ExplorationBoundary of the proposed Plan version
- **AND** approval does not authorize capability, scope or budget outside that boundary

#### Scenario: Final review is required by policy

- **WHEN** Run Policy requires human review before final delivery
- **THEN** the Run enters FINAL_REVIEW after candidate Artifacts are created and before terminal success

### Requirement: Version-bound gate request

Each GateRequest SHALL bind Run and optional Task, Gate type, authorized actor or role, scope, State Version, Plan Version, relevant Artifact Hash, creation time, expiry, allowed responses, and for PLAN_APPROVAL a hash of the approved seed/ExplorationBoundary payload.

#### Scenario: Plan boundary changes while awaiting approval

- **WHEN** seed Tasks, execution mode or ExplorationBoundary hash differs from the payload bound to an open PLAN_APPROVAL Gate
- **THEN** the response cannot approve the changed Plan and the Gate is invalidated or recreated

#### Scenario: Artifact changes while awaiting approval

- **WHEN** the approved Artifact Hash differs from the current candidate Artifact Hash
- **THEN** the response cannot approve the new Artifact and the Gate is invalidated or recreated

### Requirement: Controlled resume

A valid Gate response SHALL create a durable Resume Event and new execution claim; it MUST NOT reuse an expired Lease or original process stack. Approved agent-loop research MUST resume according to the persisted Plan execution mode and ExplorationBoundary.

#### Scenario: Approved agent-loop plan resumes

- **WHEN** a valid PLAN_APPROVAL response for a current agent-loop Plan is consumed
- **THEN** the Run records the decision, materializes/activates the approved seed Tasks and resumes with new execution claims
- **AND** no Loop action can expand the approved boundary

#### Scenario: Boundary expansion is requested later

- **WHEN** later research requires capability, scope or budget outside the approved ExplorationBoundary
- **THEN** the existing approval does not authorize that expansion
- **AND** a formal versioned Plan/Gate path is required
