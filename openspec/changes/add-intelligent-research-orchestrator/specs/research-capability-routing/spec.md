## ADDED Requirements

### Requirement: Capability registry
The system SHALL register each Capability with stable ID, version, input/output schema, permissions, timeout,
cost metadata, concurrency metadata, Adapter binding, and health status.

#### Scenario: List registered capabilities
- **WHEN** an operator requests the Capability Registry
- **THEN** the system returns descriptors without exposing Adapter secrets or internal sibling storage

#### Scenario: Capability schema is incompatible
- **WHEN** a Worker request does not satisfy the registered input schema
- **THEN** the invocation is rejected before the Adapter is called

### Requirement: Policy-constrained capability routing
CapabilityRouter SHALL select only an implementation compatible with Worker allowlist, Run Policy, system
policy, data scope, availability, and remaining budget.

#### Scenario: Web is disabled
- **WHEN** a Task requests Web Research and Run Policy does not explicitly allow Web
- **THEN** routing is denied without making a network request

#### Scenario: Compatible adapter is switched
- **WHEN** Registry configuration selects a different compatible Adapter for the same Capability
- **THEN** Core orchestration behavior and Capability DTO contracts remain unchanged

### Requirement: Sibling project isolation
Memory and RAG Adapters SHALL call only public Python APIs and MUST NOT access sibling databases, `.env` files,
internal repositories, or mutable runtime objects.

#### Scenario: Memory recall is invoked
- **WHEN** a Worker requests authorized Memory Recall
- **THEN** Memory Adapter calls the public MemoryService recall boundary and maps its result to CapabilityResult

#### Scenario: RAG query is invoked
- **WHEN** a Worker requests authorized local knowledge research
- **THEN** RAG Adapter calls the public QueryPipeline or service boundary and preserves citations and degradation metadata

### Requirement: Unified async capability result
Every Adapter SHALL expose an async invocation contract and return a normalized CapabilityResult containing
status, evidence or data, source, timing, usage, degradation, operation identity, and typed failure.

#### Scenario: Synchronous sibling service is used
- **WHEN** an Adapter wraps a synchronous Memory or RAG API
- **THEN** it executes through a bounded async bridge without blocking unrelated concurrent research Tasks

#### Scenario: Adapter returns a provider error
- **WHEN** an external provider returns an error
- **THEN** the Adapter maps it to a stable FailureCode, retryability, safe diagnostic, and outcome certainty

### Requirement: Parallel research lanes
The scheduler SHALL allow independent Memory, RAG, and Web research Tasks to execute concurrently within the
effective concurrency and budget limits.

#### Scenario: Three independent lanes are ready
- **WHEN** Memory, RAG, and authorized Web Tasks have no unmet dependencies
- **THEN** the scheduler may execute them concurrently up to the configured concurrency limit

#### Scenario: One optional lane fails
- **WHEN** an Optional research lane fails and policy permits degradation
- **THEN** successful Branch results remain accepted and the Run records the missing lane

### Requirement: Evidence join
Evidence Join SHALL use only current Plan Branches and accepted Attempt results, preserve source and timestamp,
deduplicate by source identity, mark conflicts, distinguish Required and Optional lanes, and calculate
sufficiency.

#### Scenario: Duplicate source appears in two lanes
- **WHEN** RAG and Web return evidence derived from the same source identity
- **THEN** Join deduplicates the evidence without counting it as two independent sources

#### Scenario: Required evidence conflicts
- **WHEN** accepted Required evidence contains an unresolved material conflict
- **THEN** Join marks the EvidenceSet conflicted and triggers configured research, Gate, partial, or failure handling

### Requirement: Untrusted evidence isolation
Memory, RAG, and Web content SHALL be treated as untrusted Evidence and MUST NOT directly create Control
Instructions, Capability permissions, or system configuration.

#### Scenario: Web page contains tool instructions
- **WHEN** retrieved content instructs the Agent to ignore policy or call an unauthorized tool
- **THEN** the content remains quoted Evidence and no corresponding control action is authorized

### Requirement: Read-only first-release capability set
The first release SHALL NOT register publication, email, payment, deployment, code execution, or file mutation
Capabilities.

#### Scenario: Planner proposes a write action
- **WHEN** a Plan Proposal requests a write-side-effect Capability
- **THEN** PlanValidator rejects the Plan because the Capability is absent or prohibited
