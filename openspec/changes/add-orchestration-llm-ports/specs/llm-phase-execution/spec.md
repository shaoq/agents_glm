## ADDED Requirements

### Requirement: Model-backed phase ports emit structured output via function calling

The five model-backed phase ports SHALL invoke the MODEL capability through function calling. Each port defines the JSON Schema of its output domain model as a single tool definition and parses the model tool call arguments into the typed model via Pydantic validation.

#### Scenario: Goal phase produces a validated GoalSpec
- **WHEN** the Goal phase executes with a real LLM-backed GoalNormalizer
- **THEN** the model is called with a tool whose parameters schema matches GoalNormalizationOutcome
- **AND** the returned tool call arguments parse into a Pydantic-validated GoalSpec and CompletionContract
- **AND** the phase accepts the normalized goal and transitions to PLANNING

#### Scenario: Planner produces a validated PlanProposal
- **WHEN** the Planning phase executes with a real LLM-backed Planner
- **THEN** the model returns a tool call whose arguments parse into a PlanProposal containing TaskSpec entries
- **AND** the deterministic PlanValidator and PlanAcceptor validate and materialize Tasks unchanged

### Requirement: Port failures degrade without faking success

A port SHALL surface any MODEL capability failure, unparseable output, or provider exception as a provider error. The port MUST NOT emit a fabricated Proposal.

#### Scenario: Unparseable model output degrades to IDLE
- **WHEN** the model returns a tool call whose arguments fail Pydantic validation
- **THEN** the port raises a provider error and the phase returns IDLE with a failure_code
- **AND** no Proposal is accepted and no Run state transition occurs

#### Scenario: Provider outage degrades to IDLE
- **WHEN** the MODEL capability returns a failed CapabilityResult such as timeout or upstream error
- **THEN** the phase returns IDLE with the failure code and the Run remains in its current phase

### Requirement: Accepted research evidence is persisted for downstream phases

The Task Runtime accept step SHALL persist accepted CapabilityResult evidence so that the evidence_provider can read real research output for the Research Join and Analysis phases. Persistence MUST occur inside the same atomic write transaction as the Task and Attempt transition, Event, Checkpoint, and Outbox records.

#### Scenario: Accepted evidence is readable by evidence_provider
- **WHEN** a research Task attempt is accepted with evidence in its CapabilityResult
- **THEN** the evidence is persisted within the accept transaction
- **AND** evidence_provider for that run returns the persisted evidence for the Research Join

#### Scenario: Evidence persistence is atomic with the accept transaction
- **WHEN** the accept transaction rolls back
- **THEN** no accepted evidence is visible to evidence_provider

### Requirement: Research phase runs on an LLM knowledge source

Until real Memory and RAG adapters are wired, the Research phase SHALL obtain evidence from an LLM knowledge source. The EvidenceResearcher Task is backed by the MODEL capability and every such Evidence item SHALL be labelled source_kind MODEL and is_untrusted True with a source_id identifying the model. The run-summary MUST disclose that research evidence originates from model knowledge rather than external retrieval.

#### Scenario: LLM-sourced evidence is untrusted and disclosed
- **WHEN** the Research phase produces evidence via the LLM knowledge source
- **THEN** every Evidence item has source_kind MODEL and is_untrusted True
- **AND** the run-summary records that research evidence originates from model knowledge

### Requirement: OpenAIModelAdapter supports function calling

The OpenAIModelAdapter SHALL support a tools invocation mode in addition to the existing plain-text mode. The adapter MUST return the parsed tool call in CapabilityResult data and MUST read the api_key only at the adapter boundary from the supplied ModelProfile. The api_key MUST NOT appear in prompts, events, checkpoints, artifacts, diagnostics, or logs.

#### Scenario: tools mode returns a parsed tool call
- **WHEN** the adapter is invoked with a tools parameter
- **THEN** it returns a CapabilityResult whose data contains the model tool_call name and arguments

#### Scenario: Secret is never leaked
- **WHEN** any CapabilityResult, Event, Checkpoint, Artifact, or log is produced
- **THEN** the api_key does not appear in any field

### Requirement: Production composition fails loudly when a port is missing

The production composition root SHALL wire real LLM ports, the Research LLM-provider, and the evidence_provider. If any required port is missing, it MUST raise CompositionError rather than silently substituting a Fake. The CLI run start SHALL default to the production composition and the offline composition MUST be reserved for the test suite.

#### Scenario: Missing port raises CompositionError
- **WHEN** build_production_coordinator is called without one of the required ports
- **THEN** it raises CompositionError listing the missing ports

#### Scenario: run start uses production composition
- **WHEN** a user runs agents-orchestration run start with a goal and without create-only
- **THEN** the service drives the production coordinator with real LLM ports rather than the offline Fake composition

### Requirement: Live model tests are opt-in

Tests that call the real glm-5.2 endpoint SHALL be marked smoke and skipped by default. The default test suite MUST make no real network calls and MUST continue to pass with the offline composition.

#### Scenario: Default suite makes no network calls
- **WHEN** the default test suite runs without ORCH_LIVE_SMOKE set
- **THEN** no test invokes the real model endpoint and all offline tests pass

#### Scenario: Smoke tests run only when explicitly enabled
- **WHEN** ORCH_LIVE_SMOKE is set to 1
- **THEN** the smoke-marked tests exercise the real glm-5.2 endpoint end to end
