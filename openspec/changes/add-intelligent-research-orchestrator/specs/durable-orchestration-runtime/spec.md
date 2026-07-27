## ADDED Requirements

### Requirement: Stable execution identities
The runtime SHALL model Run, Task, Attempt, and Capability Operation as distinct stable identities. Retry and
Resume MUST create a new Attempt without overwriting prior Attempt history.

#### Scenario: Retry creates a new attempt
- **WHEN** a retryable Task Attempt fails and retry policy allows another execution
- **THEN** the runtime creates a new Attempt ID for the same Task ID and preserves the failed Attempt record

#### Scenario: Resume preserves run identity
- **WHEN** a paused or interrupted Run resumes
- **THEN** the Run ID and Task IDs remain stable while new execution claims use new Attempt and Lease data

### Requirement: Validated state transitions
The runtime SHALL enforce explicit Run, Task, Attempt, and Gate state machines. A Worker or LLM Proposal MUST
NOT directly write a formal state transition.

#### Scenario: Illegal transition is rejected
- **WHEN** a component attempts a transition that is not allowed from the current state
- **THEN** the runtime rejects the transition without changing State Version and records a safe diagnostic

#### Scenario: Attempt success requires result acceptance
- **WHEN** an Attempt reports success
- **THEN** the Task remains incomplete until the runtime validates and accepts the Attempt result

### Requirement: Atomic durable commit
The runtime SHALL persist State Version, Task or Attempt transition, Domain Event, semantic Checkpoint, and
Outbox record atomically in SQLite when they belong to the same logical commit.

#### Scenario: Transaction fails before commit
- **WHEN** SQLite fails during a logical state commit
- **THEN** none of the State, Event, Checkpoint, or Outbox changes from that commit become visible

#### Scenario: Artifact reference is committed
- **WHEN** a transition depends on a large Artifact
- **THEN** the immutable Artifact is written first and its hash-bound ArtifactRef is included in the atomic state commit

### Requirement: Semantic checkpoints and restart recovery
The runtime SHALL create semantic Checkpoints after accepted Plans, accepted Branch results, Gate creation,
Retry Timer creation, Replan commit, and finalization. It SHALL recover Ready Work from formal state without
requiring the original process stack.

#### Scenario: Process restarts after checkpoint
- **WHEN** the process exits after a semantic Checkpoint and restarts
- **THEN** the runtime reloads formal state, rebuilds Ready Work, and continues without repeating accepted work

#### Scenario: Successful branch is not repeated
- **WHEN** a process fails after one parallel Branch result is accepted but before Join completes
- **THEN** recovery reuses the accepted Branch result and does not execute that Branch again

### Requirement: Lease fencing and late result protection
The runtime SHALL use Lease holder, expiry, and monotonically increasing Epoch to control Task execution.
Result acceptance MUST validate Active Attempt, Lease Epoch, Plan Version, and Expected State Version.

#### Scenario: Expired worker returns a result
- **WHEN** an old Attempt returns after its Lease has expired and another Attempt owns the Task
- **THEN** the runtime rejects the old result from formal state while retaining it as an Observation

#### Scenario: State changes during completion
- **WHEN** State Version changes after completion criteria were evaluated but before terminal commit
- **THEN** the compare-and-set terminal commit fails and affected criteria are evaluated again

### Requirement: Bounded retry, deadline, and budget
The runtime SHALL persist and enforce per-Task Attempt limit, Retry Backoff, Run Deadline, Task and Run budget,
maximum Task count, Plan depth, concurrency, Replan count, and report revision count.

#### Scenario: Retry budget is exhausted
- **WHEN** a Task reaches its maximum Attempt count
- **THEN** the runtime does not schedule another Attempt and applies the configured fail, replan, degrade, or escalate policy

#### Scenario: Resume does not reset budget
- **WHEN** a Run resumes after Pause or process restart
- **THEN** all consumed time, token, cost, retry, replan, and revision budgets remain consumed

### Requirement: Typed terminal outcome
The runtime SHALL store execution termination, goal outcome, termination reason, and external effect status as
separate fields.

#### Scenario: Deadline stops an incomplete run
- **WHEN** Run Deadline expires before the Completion Contract is satisfied
- **THEN** the Run terminates with a deadline reason and does not report a successful goal outcome

#### Scenario: Required result remains unknown
- **WHEN** a required Capability Operation has an unknown outcome during final verification
- **THEN** the Run cannot enter clean COMPLETED and enters an explicit waiting, partial, failed, escalated, or reconciliation state

### Requirement: Single-process local runtime driver
The first release SHALL support one continuous local Runtime Watch process and SHALL support one bounded Tick
for an explicitly selected Run.

#### Scenario: Tick a selected run
- **WHEN** an operator invokes `runtime tick RUN_ID`
- **THEN** the runtime executes one bounded Tick only for that Run and exits

#### Scenario: Watch all runnable runs
- **WHEN** an operator invokes `runtime watch` without a Run filter
- **THEN** one local process polls SQLite and advances all eligible Runs according to scheduler policy
