## ADDED Requirements

### Requirement: Supported human gate types
The system SHALL support GOAL_CLARIFICATION, PLAN_APPROVAL, CONFLICT_RESOLUTION, and FINAL_REVIEW Gate types,
each with a typed request and response schema.

#### Scenario: Ambiguous goal requires clarification
- **WHEN** GoalNormalizer cannot safely resolve a material ambiguity
- **THEN** it creates a GOAL_CLARIFICATION Gate with explicit questions and allowed response fields

#### Scenario: Final review is required by policy
- **WHEN** Run Policy requires human review before final delivery
- **THEN** the Run enters FINAL_REVIEW after candidate Artifacts are created and before terminal success

### Requirement: Version-bound gate request
Each GateRequest SHALL bind Run and optional Task, Gate type, authorized actor or role, scope, State Version,
Plan Version, relevant Artifact Hash, creation time, expiry, and allowed responses.

#### Scenario: Artifact changes while awaiting approval
- **WHEN** the approved Artifact Hash differs from the current candidate Artifact Hash
- **THEN** the response cannot approve the new Artifact and the Gate is invalidated or recreated

### Requirement: Durable pause
Creating a Gate SHALL atomically persist the Gate, waiting state, Event, Checkpoint, and Outbox notification
before releasing execution resources.

#### Scenario: Process exits while waiting
- **WHEN** the process exits after a Gate is committed
- **THEN** the waiting Run remains recoverable without holding a Worker, thread, connection, or Lease

### Requirement: Authorized and single-use response
Gate response handling SHALL validate actor, role, scope, response schema, expiry, State Version, Plan Version,
and Artifact Hash. A valid response SHALL be consumed at most once.

#### Scenario: Duplicate response is delivered
- **WHEN** the same response Request ID is delivered more than once
- **THEN** only the first valid delivery changes state and later deliveries return an already-consumed result

#### Scenario: Unauthorized actor responds
- **WHEN** an actor outside the allowed identity or role submits a response
- **THEN** the response is rejected and the Run remains waiting

### Requirement: Controlled resume
A valid Gate response SHALL create a durable Resume Event and new execution claim; it MUST NOT reuse an expired
Lease or original process stack.

#### Scenario: Approved plan resumes
- **WHEN** a valid PLAN_APPROVAL response is consumed
- **THEN** the Run records the decision and resumes from formal state with a new execution claim

### Requirement: Gate expiry and escalation
Every Gate SHALL have an explicit expiry or inherit a bounded Run Deadline. On expiry, the system SHALL apply
configured cancel, fail, partial, default, or escalate policy.

#### Scenario: Human does not respond before expiry
- **WHEN** Gate expiry is reached without a valid response
- **THEN** the Gate enters EXPIRED and the Run follows the configured terminal or escalation action

#### Scenario: Default action is not authorized
- **WHEN** no explicit default response is allowed for the Gate type
- **THEN** expiry cannot fabricate an approval
