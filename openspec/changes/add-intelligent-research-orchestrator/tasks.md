## 1. Package Scaffold and Architecture Boundaries

- [x] 1.1 Create `agents_orchestration` Python 3.12 package, Hatchling build, Typer entry point, test directories, `.env.example`, and storage/artifact ignore rules
- [x] 1.2 Add Pydantic, pydantic-settings, Typer, Rich, pytest, pytest-asyncio, pytest-cov, and Ruff configuration consistent with sibling projects
- [x] 1.3 Create Application, Domain, Orchestration, Runtime, Workers, Capabilities, and Adapters package boundaries
- [x] 1.4 Add architecture tests that forbid Domain/Core imports of infrastructure providers and sibling project implementations
- [x] 1.5 Add architecture tests that allow `agents_memory` and `agents_rag` imports only inside their dedicated Adapters
- [x] 1.6 Add a minimal README describing installation, first-release scope, read-only boundary, and default offline test behavior

## 2. Domain Models and State Machines

- [ ] 2.1 Add immutable identifiers and models for Run, Task, Attempt, Operation, Plan Version, State Version, Lease Epoch, Gate, Event, Checkpoint, and ArtifactRef
- [ ] 2.2 Add GoalSpec, CompletionContract, CompletionCriterion, RunPolicy, Budget, PlanGraph, TaskSpec, and Dependency models
- [ ] 2.3 Add WorkerDefinition, CapabilityDescriptor, CapabilityRequest, CapabilityResult, Evidence, EvidenceSet, and TaskResult models
- [ ] 2.4 Add typed Run, Task, Attempt, Gate, Completion, Termination, Effect, and Failure enums
- [ ] 2.5 Implement and unit-test Run state transition validation
- [ ] 2.6 Implement and unit-test Task state transition validation
- [ ] 2.7 Implement and unit-test Attempt result acceptance states
- [ ] 2.8 Implement and unit-test Gate state transitions and single-use consumption state
- [ ] 2.9 Add Domain Event models for every formal transition used by Runtime and control surfaces

## 3. Persistence and Artifact Infrastructure

- [ ] 3.1 Define Repository, Transaction, EventStore, Outbox, ArtifactStore, Clock, and ID Generator Ports
- [ ] 3.2 Implement SQLite schema creation and version tracking for all logical Runtime tables
- [ ] 3.3 Implement SQLite repositories for Run, Goal, Completion Contract, Plan, Task, Dependency, Attempt, and Lease
- [ ] 3.4 Implement SQLite repositories for Capability Call, Gate, Checkpoint, Event, Outbox, Artifact Metadata, and Request Deduplication
- [ ] 3.5 Implement compare-and-set State Version updates and Lease Epoch fencing
- [ ] 3.6 Implement atomic transaction support for State transition, Checkpoint, Event, and Outbox records
- [ ] 3.7 Implement content-hashed immutable local Artifact Store and Artifact metadata validation
- [ ] 3.8 Add orphan Artifact detection for files written before a failed SQLite transaction
- [ ] 3.9 Add integration tests proving rollback prevents partial State/Event/Checkpoint/Outbox visibility

## 4. Durable Runtime Core

- [ ] 4.1 Implement Scheduler Ready Work calculation from formal Task and dependency state
- [ ] 4.2 Implement Lease claim, renewal, expiry, release, and monotonic Epoch behavior
- [ ] 4.3 Implement BudgetGuard for Deadline, Task count, depth, concurrency, Attempt, Replan, revision, token, and cost limits
- [ ] 4.4 Implement durable Retry classification, retry budget consumption, Backoff Timer, and wake-up handling
- [ ] 4.5 Implement semantic Checkpoint creation for Plan, Branch result, Gate, Retry, Replan, and finalization boundaries
- [ ] 4.6 Implement RecoveryManager that expires stale claims, inspects unknown calls, and rebuilds Ready Work
- [ ] 4.7 Implement Attempt result validation against Active Attempt, Lease Epoch, Plan Version, State Version, and Task supersession
- [ ] 4.8 Implement Late Result rejection with Observation retention
- [ ] 4.9 Implement one bounded Runtime Tick for an explicitly selected Run
- [ ] 4.10 Implement single-process Runtime Watch for one Run and for all eligible Runs
- [ ] 4.11 Add restart, Lease loss, Late Result, Deadline, and duplicate Event integration tests

## 5. Goal Normalization and Dynamic Planning

- [ ] 5.1 Define GoalNormalizer and Planner Model Ports with structured Proposal outputs
- [ ] 5.2 Implement GoalSpec and Completion Contract schema validation
- [ ] 5.3 Implement material ambiguity detection and GOAL_CLARIFICATION Proposal
- [ ] 5.4 Implement PlanGraph Proposal parsing without Task materialization side effects
- [ ] 5.5 Implement deterministic DAG, cycle, Task contract, Registry, permission, budget, depth, concurrency, and deliverable-path validation
- [ ] 5.6 Implement Plan acceptance that atomically stores Plan Version and materializes Tasks and dependencies
- [ ] 5.7 Implement Plan rejection diagnostics without partial Task creation
- [ ] 5.8 Implement versioned Completion Contract amendment with actor, reason, and invalidated validations
- [ ] 5.9 Implement bounded Replan with preserved accepted work, precise dependency invalidation, and SUPERSEDED Tasks
- [ ] 5.10 Add unit/property tests for Plan limits, cycles, unsupported capabilities, missing deliverables, and Replan preservation

## 6. Worker and Capability Core

- [ ] 6.1 Implement Worker Registry and initial ResearchPlanner, EvidenceResearcher, Analyst, ReportWriter, and ReportReviewer definitions
- [ ] 6.2 Implement WorkerExecutor with Task-scoped Context Projection and output schema validation
- [ ] 6.3 Ensure Worker output remains a TaskResult or Proposal and cannot directly access Runtime repositories
- [ ] 6.4 Implement Capability Registry with descriptor version, schema, permission, timeout, cost, concurrency, Adapter, and health metadata
- [ ] 6.5 Implement CapabilityRouter enforcement for Worker allowlist, Run Policy, system policy, data scope, availability, and budget
- [ ] 6.6 Implement Async Capability invocation and normalized CapabilityResult/Failure mapping
- [ ] 6.7 Implement Model Profile routing for GoalNormalizer, Planner, Reviewer, and model-backed Workers
- [ ] 6.8 Add contract tests shared by Fake and real Capability Adapters
- [ ] 6.9 Add tests proving Planner and untrusted evidence cannot expand Capability permissions

## 7. Research Capability Adapters

- [ ] 7.1 Implement deterministic Fake Memory, RAG, Web, and Model Adapters for default tests
- [ ] 7.2 Implement Memory Recall Adapter using only the public `MemoryService` boundary
- [ ] 7.3 Preserve Memory scope, evidence, sufficiency, conflict, and degradation fields in CapabilityResult
- [ ] 7.4 Implement RAG Adapter using only the public `QueryPipeline` or service boundary
- [ ] 7.5 Preserve RAG citations, sources, confidence/sufficiency, and degradation fields in CapabilityResult
- [ ] 7.6 Implement policy-disabled-by-default Web Research Adapter with allowed-domain enforcement
- [ ] 7.7 Implement OpenAI-compatible Model Adapter with named profiles, usage, timeout, retry-safe diagnostics, and secret redaction
- [ ] 7.8 Wrap synchronous Memory/RAG calls in a bounded async bridge and test that unrelated Tasks remain concurrent
- [ ] 7.9 Add Adapter health checks and safe diagnostics for `capability doctor`
- [ ] 7.10 Add architecture tests proving Adapters do not read sibling databases or `.env` files

## 8. Parallel Research and Evidence Join

- [ ] 8.1 Implement stable Branch identities and Required, Optional, Conditional, Any-of, and Quorum roles
- [ ] 8.2 Implement bounded concurrent dispatch of independent Memory, RAG, and authorized Web Tasks
- [ ] 8.3 Persist each accepted Branch result independently before Join
- [ ] 8.4 Implement Evidence normalization with source identity, timestamp, freshness, citation, trust, and degradation metadata
- [ ] 8.5 Implement source-identity deduplication without overstating independent evidence count
- [ ] 8.6 Implement material conflict detection and conflict-preserving EvidenceSet output
- [ ] 8.7 Implement Required/Optional Branch aggregation and Evidence Sufficiency states
- [ ] 8.8 Implement configured behavior for optional lane failure, required lane failure, and unresolved conflict
- [ ] 8.9 Add parallel failure/restart tests proving accepted Branches are not repeated

## 9. Human Gate and Controlled Resume

- [ ] 9.1 Implement typed GOAL_CLARIFICATION, PLAN_APPROVAL, CONFLICT_RESOLUTION, and FINAL_REVIEW Gate schemas
- [ ] 9.2 Implement Gate creation bound to actor/role, scope, State Version, Plan Version, Artifact Hash, expiry, and allowed response
- [ ] 9.3 Atomically persist Gate, waiting state, Event, Checkpoint, and Outbox before releasing execution resources
- [ ] 9.4 Implement actor, role, scope, schema, expiry, version, and Artifact validation for Gate responses
- [ ] 9.5 Implement Request ID deduplication and at-most-once Gate response consumption
- [ ] 9.6 Implement Resume Event creation with new Attempt/Lease claim rather than old process continuation
- [ ] 9.7 Implement Gate expiry and configured cancel, fail, partial, default, or escalate actions
- [ ] 9.8 Add tests for unauthorized, stale, duplicate, expired, and Artifact-mismatched Gate responses

## 10. Analysis, Report, Review, and Finalization

- [ ] 10.1 Implement immutable EvidenceSet and AnalysisArtifact production with evidence-linked conclusions
- [ ] 10.2 Implement ReportWriter output schema for Markdown and structured report content
- [ ] 10.3 Implement ReportReviewer PASS, REVISE, RESEARCH_GAP, CONFLICT, and ESCALATE Proposals
- [ ] 10.4 Implement bounded revision handling and shared Run budget accounting
- [ ] 10.5 Connect RESEARCH_GAP and material conflict Proposals to Replan or Gate policy
- [ ] 10.6 Implement deterministic Completion Contract evaluation with SATISFIED, UNSATISFIED, UNKNOWN, and NOT_APPLICABLE
- [ ] 10.7 Implement Candidate State freeze and compare-and-set terminal commit
- [ ] 10.8 Implement Citation integrity validation against Evidence IDs and source metadata
- [ ] 10.9 Generate immutable `report.md`, `report.json`, and `run-summary.json`
- [ ] 10.10 Include degradation, unmet criteria, missing sources, unresolved conflicts, and termination reason in partial outputs
- [ ] 10.11 Add tests proving report recommendations cannot trigger write-side-effect Capabilities

## 11. Application Service and CLI

- [ ] 11.1 Implement async OrchestrationService start, drive, inspect, pause, resume, cancel, Gate, Artifact, and export methods
- [ ] 11.2 Implement idempotent StartRun Request ID behavior and stale Expected Version conflicts
- [ ] 11.3 Implement `run start`, `run show`, `run watch`, `run pause`, `run resume`, and `run cancel`
- [ ] 11.4 Implement `run start --create-only` and `--follow` display semantics
- [ ] 11.5 Implement `gate list` and `gate respond`
- [ ] 11.6 Implement `artifact list` and hash-validating `artifact export`
- [ ] 11.7 Implement `capability list` and secret-safe `capability doctor`
- [ ] 11.8 Implement `runtime tick RUN_ID`, `runtime watch --run RUN_ID`, and global `runtime watch`
- [ ] 11.9 Reject `runtime tick` without a Run ID and document that Runtime commands do not alter Pause/Gate/Cancel state
- [ ] 11.10 Map typed domain failures to stable CLI exit codes, JSON diagnostics, and human-readable Rich output
- [ ] 11.11 Add CLI and Service unit tests proving CLI delegates to Application rather than duplicating domain logic

## 12. Configuration, Security, and Observability

- [ ] 12.1 Implement pydantic-settings for SQLite, Artifact path, Model profiles, Registry, Run policy defaults, and system maximums
- [ ] 12.2 Enforce that Run Policy can only remain within or tighten system policy
- [ ] 12.3 Keep Web disabled by default and enforce allowed-domain policy before network invocation
- [ ] 12.4 Ensure secrets are loaded only at Adapter boundaries and redacted from Prompt, State, Event, Checkpoint, Artifact, diagnostics, and logs
- [ ] 12.5 Register only first-release read-only Capabilities and add a test that rejects write Capability proposals
- [ ] 12.6 Implement untrusted Evidence labeling and separation from Control Instructions
- [ ] 12.7 Implement JSONL structured logs correlated by Run, Task, Attempt, Operation, Gate, and Plan IDs
- [ ] 12.8 Implement token, cost, latency, retry, and degradation Usage Ledger
- [ ] 12.9 Implement safe Event streaming used by `run watch`
- [ ] 12.10 Add security tests for prompt injection content, cross-scope Memory requests, domain policy, and secret redaction

## 13. End-to-End Verification and Documentation

- [ ] 13.1 Add Fake-based E2E test for Goal → Plan → parallel research → Join → Analyze → Write → Review → Finalize
- [ ] 13.2 Add E2E test for GOAL_CLARIFICATION, PLAN_APPROVAL, CONFLICT_RESOLUTION, and FINAL_REVIEW
- [ ] 13.3 Add E2E test for evidence-gap Replan preserving unaffected accepted results
- [ ] 13.4 Add E2E test for Memory, RAG, Web, and Model degradation combinations
- [ ] 13.5 Add failure-injection tests at Plan commit, Task dispatch, Branch commit, Gate response, Replan, Artifact write, and final verification boundaries
- [ ] 13.6 Add restart test proving a Run resumes from SQLite and Artifact state in a fresh process
- [ ] 13.7 Add optional, explicitly enabled live Smoke Tests for Model, Memory, RAG, and Web Adapters
- [ ] 13.8 Verify default tests make no real network calls and meet the configured coverage threshold
- [ ] 13.9 Run Ruff, unit, contract, integration, architecture, and E2E test suites
- [ ] 13.10 Update README with CLI examples, Python API, Runtime command semantics, Gate workflow, recovery, artifacts, configuration, and limitations
- [ ] 13.11 Verify implementation behavior against all six capability specs and record any intentionally deferred behavior
