## ADDED Requirements

### Requirement: Python orchestration service
The application SHALL expose an async OrchestrationService for starting, driving, inspecting, pausing, resuming,
cancelling, responding to Gates, listing Artifacts, and exporting Artifacts.

#### Scenario: Start and drive a run
- **WHEN** a caller submits a valid StartRun command without create-only mode
- **THEN** the service creates the Run and drives it until terminal, paused, or waiting state

#### Scenario: Create run without executing
- **WHEN** a caller submits StartRun with create-only mode
- **THEN** the service persists a CREATED Run and returns without executing Tasks

### Requirement: CLI uses the application service
The CLI SHALL adapt arguments and render results through OrchestrationService and MUST NOT duplicate planning,
state machine, capability, or recovery logic.

#### Scenario: Show a run as JSON
- **WHEN** an operator executes `run show RUN_ID --json`
- **THEN** CLI returns a stable structured Run view from OrchestrationService

### Requirement: Run control commands
The CLI SHALL provide `run start`, `run show`, `run watch`, `run pause`, `run resume`, and `run cancel`.

#### Scenario: Pause a running run
- **WHEN** an operator issues `run pause RUN_ID` with the current Expected Version
- **THEN** the service records a durable pause request and stops scheduling new Task work at a safe boundary

#### Scenario: Resume a paused run
- **WHEN** an operator issues `run resume RUN_ID` for a resumable Run
- **THEN** the service records a Resume Event and drives that Run until it blocks or terminates again

### Requirement: Gate and artifact commands
The CLI SHALL provide `gate list`, `gate respond`, `artifact list`, and `artifact export`.

#### Scenario: Respond to a gate
- **WHEN** an authorized operator submits a schema-valid Gate response
- **THEN** CLI returns the consumed or rejected result without directly modifying Gate storage

#### Scenario: Export final artifacts
- **WHEN** an operator exports a Run to a target directory
- **THEN** the service writes only the Run's authorized Artifacts and reports their hashes

### Requirement: Capability diagnostics
The CLI SHALL provide `capability list` and `capability doctor` without exposing secrets.

#### Scenario: Adapter is unavailable
- **WHEN** capability doctor checks an unavailable Adapter
- **THEN** it reports Capability ID, implementation, health state, and safe diagnostic without printing credentials

### Requirement: Runtime control commands
The CLI SHALL provide `runtime tick RUN_ID`, `runtime watch --run RUN_ID`, and global `runtime watch`.

#### Scenario: Tick requires a run
- **WHEN** an operator invokes runtime tick without a Run ID
- **THEN** CLI rejects the command without advancing any Run

#### Scenario: Watch a selected run
- **WHEN** an operator invokes `runtime watch --run RUN_ID`
- **THEN** the local Runtime Driver advances only that Run until it blocks, terminates, or the process stops

### Requirement: Idempotent and versioned control
Mutating service and CLI operations SHALL use Request ID or Expected State Version to prevent duplicate or
stale control actions.

#### Scenario: Duplicate start request
- **WHEN** the same StartRun Request ID and equivalent payload are submitted twice
- **THEN** the service returns the existing Run result instead of creating a second Run

#### Scenario: Stale pause request
- **WHEN** a pause command uses an older Expected State Version
- **THEN** the service rejects it with ConcurrencyConflict

### Requirement: Typed diagnostics and exit behavior
The control surface SHALL map ValidationError, PolicyDenied, CapabilityFailure, ConcurrencyConflict,
RecoveryRequired, and TerminalRunError to stable structured diagnostics and CLI exit behavior.

#### Scenario: Diagnostic contains sensitive provider text
- **WHEN** an underlying exception includes a secret or credential-like value
- **THEN** the surfaced diagnostic is redacted before logging or CLI output

### Requirement: Structured observability
The control surface SHALL expose Run-correlated Domain Events and usage summaries for Run, Task, Attempt,
Capability Operation, Gate, Plan, retry, degradation, token, cost, and latency.

#### Scenario: Watch run progress
- **WHEN** an operator invokes `run watch RUN_ID`
- **THEN** CLI streams ordered safe events correlated to that Run without treating logs as formal state
