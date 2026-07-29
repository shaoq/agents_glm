## Why

生产代码同时维护 **production** 与 **offline/fake** 两套装配，差异仅在「是否接真实 Adapter」。CLI 实际已只走
production（`cli.py` 永远 `production=True`），offline/fake 仅服务于测试，却长期驻留在 `src/`：

- `adapters/fake.py` 把四个模拟后端（`FakeMemory/RAG/Web/ModelAdapter`）+ `build_fake_registry()` 编进生产包；
- `composition.py` 的 `_Fake*` 阶段端口与 `build_offline_coordinator` 把模拟装配和生产装配并排放在同一模块；
- `OrchestrationService` 携带 `production: bool` 开关，且默认 `capability_registry = build_fake_registry()`——
  导致 **production 模式下 `capability list` 仍误报 fake** 适配器（latent bug）。

这与「只保留接真实 Adapter 的生产级应用」的产品定位冲突。本次把 offline/fake 从生产代码彻底移除，单一保留生产装配；
确定性测试能力通过既有显式端口缝（`build_production_coordinator`）与真实 adapter 的注入缝（`recall_fn`/`query_fn`/
`fetch_fn`）下沉到 `tests/`——**生产代码与测试替身彻底分离，且无任何 Fake 类存活**。

## What Changes

- **BREAKING** 删除 `src/agents_orchestration/adapters/fake.py` 整文件（`FakeMemoryAdapter`/`FakeRAGAdapter`/
  `FakeWebAdapter`/`FakeModelAdapter`/`build_fake_registry`）。
- **BREAKING** 删除 `orchestration/composition.py` 中的 `_FakeGoalNormalizer`/`_FakePlanner`/`_FakeExecutor`/
  `_FakeAnalyst`/`_FakeWriter`/`_FakeReviewer`、5 个 raw provider 函数与 `build_offline_coordinator`。
  保留 `build_production_coordinator`（显式端口装配，天然测试注入缝）、`build_production_coordinator_from_settings`、
  `CompositionError`、`_LLMResearchHandler`、`_NoopHandler`。
- **BREAKING** `OrchestrationService` 移除 `production: bool` 参数与 `_production` 分支；`coordinator` 永远走生产装配；
  新增 `coordinator=None` 注入缝，替代当前测试里 `service._coordinator = ...` 的私有属性 hack。
- 修复 `application/service.py` 默认 `capability_registry`：由 `build_fake_registry()` 改为空 `CapabilityRegistry()`
  （顺带消除 production 下 `capability list` 误报 fake 的 latent bug；与生产 coordinator 自建的空 registry 一致）。
- `cli.py` 去掉 `production=True`（production 成为唯一模式）；`adapters/__init__.py` docstring 去掉「Fake」字样。
- 测试侧（`tests/`，非生产代码）：
  - `_Fake*` 阶段端口 + `build_deterministic_coordinator`（= 原 `build_offline_coordinator` 改名搬迁，内部调
    `build_production_coordinator` 注入 double）下沉到 `tests/support/deterministic.py`；
  - `build_fake_registry` 由真实 adapter（`MemoryRecallAdapter`/`RagAdapter`/`WebResearchAdapter` + 确定性
    `recall_fn`/`query_fn`/`fetch_fn`）构成的 `build_test_registry()` 替代，下沉到 `tests/support/test_registry.py`；
  - 改写受影响测试：`tests/integration/test_composition.py`、`tests/integration/test_adapters.py`、
    `tests/e2e/conftest.py`、`tests/e2e/test_public_boundary_scenarios.py`、`tests/e2e/test_gates_and_degradation_e2e.py`。
- **范围外（保持现状）**：`memory.py`/`rag.py`/`web.py` 三个真实 adapter 当前未被生产装配接线（生产路径只接 Model/LLM），
  本次不补齐，留给独立 change。

## Capabilities

### New Capabilities

- _None._

### Modified Capabilities

- `orchestration-control-surface`: 新增「单一生产装配」requirement——生产代码 SHALL 只保留接真实 Adapter 的生产
  装配，MUST NOT 在生产代码中保留 offline/fake 模拟装配或模拟后端；`OrchestrationService` SHALL 移除
  production/offline 开关，确定性测试通过显式端口注入缝实现。

## Impact

- **生产代码**：`adapters/fake.py`（删）、`orchestration/composition.py`（删 offline 段）、`application/service.py`
  （去开关 + 注入缝 + 默认 registry 修复）、`cli.py`（去 `production=True`）、`adapters/__init__.py`（docstring）。
- **测试**：新增 `tests/support/deterministic.py`、`tests/support/test_registry.py`；改写 5 个测试文件；移除
  `service._coordinator` 私有 hack 的 2 处直接赋值。
- **行为变化（预期代价）**：本地无 `.env` / LLM key 时，`OrchestrationService` 驱动任意 run 会在首个 LLM 端口调用处
  失败——这是 production-only、无 fake 的直接结果。README 需补一句 dev-mode 说明。
- **依赖关系**：无新增第三方依赖。与在途 `add-orchestration-llm-ports` 剩余 2 个 task（5.3 live smoke / 5.5 README）
  不冲突；5.5 的「deferred Memory/RAG note」可由本次承接补充。
