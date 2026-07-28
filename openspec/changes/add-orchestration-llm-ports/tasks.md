## 1. Function-Calling Model Adapter

- [ ] 1.1 Extend `OpenAIModelAdapter` with `invoke_tools(request, tools)`: pass `tools` to `chat.completions.create`, parse `tool_calls[0]` into `CapabilityResult.data={"tool_name", "arguments"}`, preserve usage/timing; keep plain-text `invoke` for long-form output
- [ ] 1.2 Handle `tools`-mode failure paths (no tool_call / empty arguments / provider error) → `CapabilityResult.failed(retryable=True)`; never raise
- [ ] 1.3 Add a JSON-schema helper that derives a tool definition from a Pydantic model (name + description + `parameters` from `model.model_json_schema()`)
- [ ] 1.4 Unit-test `invoke_tools` with a stubbed OpenAI client (tool_call present / absent / malformed); assert secret is not in `CapabilityResult`

## 2. Accepted-Evidence Persistence

- [ ] 2.1 Decide store: add an `evidence` persistence path (new table/repo preferred for queryability) within the existing UnitOfWork; large content stays in artifacts, the evidence record carries text + source + citation + trust + run_id + attempt_id
- [ ] 2.2 Persist accepted evidence inside `RuntimeTick._accept_success` (same atomic transaction as Task/Attempt/Event/Checkpoint/Outbox); rollback must hide it
- [ ] 2.3 Implement `evidence_provider(run_id)` reading the persisted accepted evidence for Research Join and Analysis
- [ ] 2.4 Integration-test: accept a Task with evidence → provider returns it; rollback → provider returns nothing (atomicity)

## 3. LLM Phase Ports (function calling)

- [ ] 3.1 `LLMGoalNormalizer`: prompt + tool schema for `GoalNormalizationOutcome`; material ambiguity → `GoalClarificationProposal`; parse → Pydantic; failure raises provider error (handler degrades)
- [ ] 3.2 `LLMPlanner`: prompt + tool schema for `PlanProposal` (TaskSpec[] + deliverables); parse → Pydantic; deterministic PlanValidator/PlanAcceptor unchanged
- [ ] 3.3 `LLMAnalyst`: prompt + tool schema for `AnalysisArtifact` (evidence-linked conclusions); receives `EvidenceSet`
- [ ] 3.4 `LLMReportWriter`: long-form markdown — evaluate function calling vs plain-text `invoke` with `[N]` citation markers; choose the more reliable for long output (Open Question)
- [ ] 3.5 `LLMReportReviewer`: prompt + tool schema for `ReviewProposal` (PASS/REVISE/RESEARCH_GAP/CONFLICT/ESCALATE); route verdict via existing `map_review_verdict`
- [ ] 3.6 Research LLM knowledge source (R1): `EvidenceResearcher` Task backed by MODEL capability, producing `Evidence(source_kind=MODEL, is_untrusted=True)`; run-summary discloses model-knowledge origin

## 4. Production Composition & CLI

- [ ] 4.1 `build_production_coordinator(backend, settings)`: register `OpenAIModelAdapter` as MODEL capability; wire 5 LLM ports + Research LLM-provider + evidence_provider; Memory/RAG/Web stay Fake placeholders (TODO comment for sibling change)
- [ ] 4.2 Fail-loudly: missing required port → `CompositionError`; do not silently substitute Fake
- [ ] 4.3 CLI `run start` defaults to production composition; `--create-only` unchanged; offline composition only for tests
- [ ] 4.4 Architecture test: adapters still confine sibling/provider imports; secret redaction covers new tools-mode result

## 5. Testing & Verification

- [ ] 5.1 Offline unit tests for each port (stubbed LLM returning fixed tool_call JSON): parse success, validation failure → degrade, provider failure → degrade
- [ ] 5.2 Offline integration: full Goal→Plan→Research→Analyze→Write→Review→Finalize with stubbed LLM + evidence persistence
- [ ] 5.3 Live smoke (`@pytest.mark.smoke`, `ORCH_LIVE_SMOKE=1`): real `glm-5.2` end-to-end, produces a non-empty `report.md` with citations; skipped by default
- [ ] 5.4 Verify default suite makes no network calls; run ruff + full test suite green; coverage threshold met
- [ ] 5.5 Update README: production usage, `run start` real-report flow, deferred Memory/RAG note, live-smoke enablement
