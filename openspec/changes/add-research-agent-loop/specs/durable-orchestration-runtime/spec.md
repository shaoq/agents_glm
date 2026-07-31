## MODIFIED Requirements

### Requirement: Stable execution identities

The runtime SHALL model Run, Task, Attempt, Agent Decision Operation, Capability Operation, ResearchLoop, ResearchDirection, and ResearchStep as distinct stable identities. Retry and Resume MUST create a new execution claim without overwriting prior Attempt or Step history; replay of the same logical ResearchStep MUST preserve its logical step ID and its separate decision/capability request idempotency keys.

#### Scenario: Research step retry creates a new attempt

- **WHEN** a retryable ResearchStep execution fails and step retry policy allows another execution
- **THEN** the runtime creates a new Attempt/Lease claim for the same Task and logical step
- **AND** preserves prior failed execution history

#### Scenario: Resume preserves loop identity

- **WHEN** a paused or interrupted agent-loop Run resumes
- **THEN** Run, Task, Loop and accepted Step IDs remain stable
- **AND** new execution claims use new Attempt/Lease data without resetting counters or usage

### Requirement: Semantic checkpoints and restart recovery

The runtime SHALL create durable DECIDING/PREPARED ResearchStep records before external Agent/capability calls and semantic Checkpoints after accepted Plans, accepted fixed-fanout Branch results, every accepted ResearchStep, Gate creation, Retry Timer creation, Replan commit, and finalization. It SHALL recover ready work from formal state without requiring the original process stack and MUST NOT repeat an accepted Agent Decision, Branch or ResearchStep effect.

#### Scenario: Process restarts after research step checkpoint

- **WHEN** the process exits after a ResearchStep checkpoint and restarts
- **THEN** Runtime reloads Loop state, Directions, coverage, Evidence IDs, usage and next step index
- **AND** continues from the next unaccepted step

#### Scenario: Capability returned before local accept

- **WHEN** process failure occurs after a capability operation returned but before ResearchStep accept committed
- **THEN** recovery reconciles the PREPARED step using the stable request ID
- **AND** does not create a second formally accepted operation, Evidence or usage charge

#### Scenario: Agent returned before action prepare

- **WHEN** process failure occurs after Agent reasoning returned but before action/actual usage was committed
- **THEN** recovery reconciles the DECIDING step using the stable decision request ID and durable reservation
- **AND** unknown outcome is conservatively accounted rather than retried without charge

### Requirement: Lease fencing and late result protection

The runtime SHALL use Lease holder, expiry, heartbeat and monotonically increasing Epoch to control Task/ResearchStep execution. Result acceptance MUST validate Active Attempt, logical Step, Lease Epoch, Plan Version and Expected State Version. A long-running Agent or capability call MUST renew its Lease before expiry; renewal failure MUST fence its result.

#### Scenario: Step lease heartbeat succeeds

- **WHEN** a current step remains active near the configured heartbeat boundary
- **THEN** Runtime renews the same Lease epoch and extends expiry without creating a new Attempt

#### Scenario: Expired worker returns a result

- **WHEN** an old Attempt returns after its Lease expired and another Attempt owns the Task/Step
- **THEN** Runtime rejects the old result from formal Evidence, Direction, usage and Task state
- **AND** retains a safe observation

#### Scenario: Plan changes during step

- **WHEN** Plan Version changes after a ResearchStep was prepared but before accept
- **THEN** the step result is fenced as stale and cannot mutate the new Plan

### Requirement: Bounded retry, deadline, and budget

The runtime SHALL persist and enforce per-logical-step failure Attempt limit for agent-loop work, per-Task Attempt limit for fixed-fanout work, Retry Backoff, Run Deadline, loop and Run budget, maximum Task count, Plan depth, concurrency, Replan count, report revision count, `max_steps`, and `max_directions`. Loop ceilings MUST remain subsets of shared Run budget and MUST NOT reset on Retry, Resume or Replan.

#### Scenario: Step retry budget is exhausted

- **WHEN** the current logical ResearchStep reaches its maximum failure Attempt count
- **THEN** Runtime does not schedule another execution of that step
- **AND** applies configured degradation, failure or exhaustion policy without consuming future step allowance

#### Scenario: Successful steps do not consume later retry allowance

- **WHEN** a Task has many accepted QUERY/ADD_DIRECTION steps and a new step fails once
- **THEN** retry admission uses the new step's failure count rather than Task historical dispatch count

#### Scenario: Replay does not double-consume budget

- **WHEN** the same accepted logical step or capability operation is replayed
- **THEN** token and cost usage remain exactly the originally accepted values

#### Scenario: Resume does not reset budget

- **WHEN** a Run resumes after Pause or process restart
- **THEN** all consumed time, token, cost, step, direction, retry, replan and revision budgets remain consumed

### Requirement: Single-process local runtime driver

The first release SHALL support one continuous local Runtime Watch process and one bounded Tick for an explicitly selected Run. For agent-loop Plans, one Tick MUST advance at most one ResearchStep per selected Task and MUST NOT execute the Task's entire remaining loop in an inner while-loop.

#### Scenario: Tick a selected agent-loop run

- **WHEN** an operator invokes `runtime tick RUN_ID`
- **THEN** Runtime executes one bounded batch for only that Run
- **AND** each selected agent-loop Task performs at most one step and one capability operation

#### Scenario: Watch all runnable runs

- **WHEN** an operator invokes `runtime watch` without a Run filter
- **THEN** one local process polls SQLite and advances eligible Runs according to scheduler, mode, concurrency and boundary policy
