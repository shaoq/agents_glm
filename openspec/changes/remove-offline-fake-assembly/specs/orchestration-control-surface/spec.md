## ADDED Requirements

### Requirement: Single production composition
The application SHALL expose exactly one composition profile in production code: a production assembly that wires real
Adapters. Production code MUST NOT contain an offline composition profile, fake or simulated phase ports, or simulated
capability backends (no `Fake*Adapter` classes, no `build_fake_registry`, no `_Fake*` phase ports, no
`build_offline_coordinator` under `src/`). The `OrchestrationService` MUST NOT expose a production/offline toggle; it
SHALL always compose the production coordinator, and SHALL provide an explicit coordinator injection seam so
deterministic tests inject a coordinator without touching production/offline flags. The service's default
capability registry MUST NOT contain fake or simulated adapters.

#### Scenario: Production source tree contains no simulated assembly
- **WHEN** the production source tree (`src/agents_orchestration/`) is inspected
- **THEN** no `adapters/fake.py`, no `Fake*Adapter` classes, no `build_fake_registry`, no `_Fake*` phase ports, and no
  `build_offline_coordinator` are present

#### Scenario: Service composes the production coordinator by default
- **WHEN** an `OrchestrationService` is constructed without an injected coordinator
- **THEN** its coordinator is the production coordinator built from settings (real LLM-backed phase ports), and no
  production/offline flag is accepted by the constructor

#### Scenario: Deterministic test injects a coordinator via the seam
- **WHEN** a test constructs `OrchestrationService` with an injected coordinator
- **THEN** the service uses that coordinator instead of building the production one, with no private-attribute assignment
  and no offline composition referenced from production code

#### Scenario: Default capability registry reports no fake adapters
- **WHEN** `capability list` / `capability doctor` is invoked on a production service constructed without an injected
  capability registry
- **THEN** the reported adapters contain no fake or simulated descriptors (the default registry is empty)
