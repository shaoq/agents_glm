## 1. Baseline and Recall contracts

- [x] 1.1 Run the current `agents_memory` unit, integration, and architecture tests and record the clean baseline
- [x] 1.2 Add failing contract tests for Recall enums, public `RecallRequest`, public `RecallResult`, and validation invariants
- [x] 1.3 Create the `agents_memory.recall` package, public/internal Recall models, stable degradation codes, and Recall domain errors
- [x] 1.4 Add serialization and immutability tests for stage contracts and verify temporary Recall fields do not modify `MemoryRecord`
- [x] 1.5 Add failing pipeline-skeleton tests for seven-stage ordering, diagnostic aggregation, recoverable failures, and fatal failures
- [x] 1.6 Implement the dependency-injected `MemoryRecallPipeline` skeleton with fake-stage support

## 2. Additive storage and index reads

- [ ] 2.1 Run GitNexus upstream impact analysis for `MemoryRepository`, report the blast radius, and stop for user direction if risk is HIGH or CRITICAL
- [x] 2.2 Add failing Repository tests for bounded batch loading and user/agent/session/type scoped reads
- [x] 2.3 Implement additive bounded batch and hierarchical read methods without changing existing Repository method semantics
- [x] 2.4 Add failing Repository tests for temporal queries, eligible historical versions, and relation batch reads
- [x] 2.5 Implement bounded temporal, historical, and relation read methods with mandatory `user_id` constraints
- [x] 2.6 Add failing Repository tests for bounded unsynced-record reads and final state/version revalidation
- [x] 2.7 Implement unsynced coverage reads and final revalidation reads without write-side effects
- [x] 2.8 Run GitNexus upstream impact analysis for the existing memory index query symbols, report the blast radius, and stop for user direction if risk is HIGH or CRITICAL
- [x] 2.9 Add failing vector adapter tests for Recall queries with required user and optional agent/session/type filters
- [x] 2.10 Add the Recall-specific `query_candidates(...)` contract and Chroma implementation while preserving existing Write `query(...)`
- [x] 2.11 Run the existing Repository, vector, lookup, coordinator, write-pipeline, sync, and maintenance regression tests

## 3. Intent construction and deterministic planning

- [ ] 3.1 Add failing tests for structured intent extraction, explicit-versus-inferred constraints, and bounded query variants
- [ ] 3.2 Implement the Recall Intent Builder protocol, GLM-4.7-Flash structured implementation, prompt, schema validation, and normalization
- [ ] 3.3 Add failing tests for timeout, malformed output, low-confidence output, and original-query fallback
- [ ] 3.4 Implement the conservative deterministic intent fallback and stable fallback diagnostics
- [ ] 3.5 Add failing Planner tests for session, agent-history, and user-shared lanes, caller narrowing, lane quotas, and global hard limits
- [ ] 3.6 Implement deterministic Recall planning with authorization-aware lanes and bounded path budgets

## 4. Candidate retrieval and eligibility

- [ ] 4.1 Add failing retrieval tests for multi-query semantic hits, per-lane quotas, global bounds, and `memory_id` deduplication with signal preservation
- [ ] 4.2 Implement semantic candidate retrieval and cross-path candidate merging without using Write `ContextLookup`
- [ ] 4.3 Add failing tests for structured temporal candidates, one-hop relation expansion, visited tracking, and expansion bounds
- [ ] 4.4 Implement temporal candidate reads and bounded one-hop relationship expansion with repeated user/scope validation
- [ ] 4.5 Add failing tests for stale Chroma records, missing-index ACTIVE records, temporary similarity, and read-only unsynced coverage
- [ ] 4.6 Implement SQLite hydration, stale-index rejection, and the bounded unsynced overlay
- [ ] 4.7 Add failing Eligibility Filter tests for cross-user records, unauthorized scopes, validity modes, explicit type/time constraints, and corrupt records
- [ ] 4.8 Implement deterministic eligibility filtering and stable rejection-reason diagnostics

## 5. Explainable utility scoring

- [ ] 5.1 Add failing tests for normalized semantic, task, temporal, scope, trust, hit-robustness, and bounded-importance score components
- [ ] 5.2 Implement deterministic base scoring with configurable weights and component-level explanations
- [ ] 5.3 Add failing tests for bounded LLM batch review, provisional evidence roles, missing components, and score re-normalization
- [ ] 5.4 Implement the GLM-4.7-Flash batch reviewer and transparent score combination without accepting an opaque model total
- [ ] 5.5 Add failing tests proving high similarity or importance cannot override low task contribution and LLM failure preserves deterministic scoring
- [ ] 5.6 Implement scoring fallback, score-confidence metadata, and candidate limits for LLM review

## 6. Temporal, relational, and conflict evidence

- [ ] 6.1 Add failing tests for event time versus record time, current-state lookup, point-in-time lookup, interval lookup, and state evolution
- [ ] 6.2 Implement deterministic temporal-view resolution using event time, validity time, and explicit query intent
- [ ] 6.3 Add failing tests for `SUPERSEDES` and `CORRECTS` chains, missing nodes, self-links, cycles, and cross-user relation rejection
- [ ] 6.4 Implement bounded explicit-relation resolution and evidence roles without mutating stored validity or relations
- [ ] 6.5 Add failing tests for same-event strong anchors, clearly separate events, and `UNKNOWN_EVENT_IDENTITY`
- [ ] 6.6 Implement candidate bucketing and bounded same-event semantic review
- [ ] 6.7 Add failing tests for natural evolution, resolvable correction, unresolved fact conflict, and unknown-identity plus contradictory content
- [ ] 6.8 Implement conservative evidence grouping that keeps key conflict sides together, does not create DEFER, and never asks the user for clarification
- [ ] 6.9 Add failing tests for LLM relation-review failure and implement explicit-relation/time-only resolution fallback

## 7. Set selection and context assembly

- [ ] 7.1 Add failing tests for direct-evidence priority, uncovered-need coverage, complementary evidence, redundancy penalties, and deterministic tie-breaking
- [ ] 7.2 Implement explainable marginal-value EvidenceGroup selection
- [ ] 7.3 Add failing tests for atomic conflict groups, long evolution-chain compression, evidence-count limits, and Token hard limits
- [ ] 7.4 Implement conflict-safe budget handling, key-history selection, and conservative token estimation fallback
- [ ] 7.5 Add failing Context Assembler tests for stable semantic sections, evidence-ID traceability, time/role/source labels, and neutral uncertainty wording
- [ ] 7.6 Implement deterministic context rendering without generative summarization or renewed evidence decisions
- [ ] 7.7 Add failing tests for `SUFFICIENT`, `PARTIAL`, `CONFLICTED`, and `EMPTY` independently from `COMPLETE` and `DEGRADED`
- [ ] 7.8 Implement result sufficiency, execution status, metadata, and diagnostic-detail gating

## 8. Pipeline degradation and consistency

- [ ] 8.1 Add failing end-to-end fake tests for intent, rewrite, embedding, Chroma, lane, scoring, resolution, and tokenizer failures
- [ ] 8.2 Implement the stable degradation matrix, global deadline, bounded retry policy, and partial-lane result handling
- [ ] 8.3 Add failing tests proving SQLite or authorization-validation failure is fatal and unvalidated candidates never enter results
- [ ] 8.4 Implement fatal-error boundaries and domain-error propagation
- [ ] 8.5 Add failing tests for immutable logical snapshots, final record revalidation, whole-group invalidation, one reselection, and persistent drift failure
- [ ] 8.6 Implement final state revalidation and at-most-once reselection without holding a long SQLite transaction
- [ ] 8.7 Add a read-only integration assertion that Recall does not change memories, relations, index operations, write requests, or pending resolutions

## 9. Service, configuration, and CLI

- [ ] 9.1 Run GitNexus upstream impact analysis for `MemoryService`, Settings validation, runtime construction, and CLI symbols before editing them; report HIGH or CRITICAL risk
- [ ] 9.2 Add failing Settings tests for Recall defaults, hard limits, GLM-4.7-Flash configuration, and lazy `validate_recall()`
- [ ] 9.3 Implement grouped Recall settings and validation without breaking storage-only or maintenance commands
- [ ] 9.4 Add failing Service tests for injected Recall Pipeline, structured results, domain-error mapping, and Recall-not-configured behavior
- [ ] 9.5 Implement `MemoryService.recall(...)` with optional Pipeline injection
- [ ] 9.6 Add failing CLI tests for query/scope/time/type/budget arguments, human-readable output, JSON output, diagnostics, and failures
- [ ] 9.7 Implement CLI `recall` using the same Service/Pipeline runtime and no duplicate recall logic
- [ ] 9.8 Update package exports and CLI help while keeping existing commands and imports backward compatible

## 10. End-to-end verification and documentation

- [ ] 10.1 Add integration fixtures using real SQLite plus Fake Embedder, Fake Index, and Fake LLM
- [ ] 10.2 Add end-to-end tests for current-session, agent-history, user-shared, current-state, point-in-time, evolution, correction, conflict, and unknown-event-identity scenarios
- [ ] 10.3 Add end-to-end tests for stale index, missing index, total LLM fallback, partial lane failure, cross-user rejection, budget pressure, concurrent drift, and complete empty results
- [ ] 10.4 Extend architecture tests to reject imports, environment reads, database paths, or Chroma collections from sibling projects
- [ ] 10.5 Run the full `agents_memory` test suite and correct only Recall-related regressions
- [ ] 10.6 Update Recall implementation documentation, configuration example, package description, and CLI usage so they match delivered behavior
- [ ] 10.7 Run `openspec validate add-memory-recall-pipeline --strict` and fix all proposal/spec consistency errors
- [ ] 10.8 Run `gitnexus_detect_changes()` before commit, review affected symbols and execution flows, and confirm they match this proposal
- [ ] 10.9 Run final formatting, static checks, `git diff --check`, and the complete test suite, recording evidence for handoff
