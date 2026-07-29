## 1. 测试替身下沉到 `tests/support`（生产代码尚不动）

- [x] 1.1 新增 `tests/support/deterministic.py`：从 `composition.py` 搬入 `_FakeGoalNormalizer`/`_FakePlanner`/
      `_FakeExecutor`/`_FakeAnalyst`/`_FakeWriter`/`_FakeReviewer` + 5 个 raw provider 函数，并提供
      `build_deterministic_coordinator(backend)`（内部调 `build_production_coordinator` 注入这些 double）
- [x] 1.2 新增 `tests/support/test_registry.py`：`build_test_registry()` 用真实 `MemoryRecallAdapter`/`RagAdapter`/
      `WebResearchAdapter` + 确定性 `recall_fn`/`query_fn`/`fetch_fn`（web 默认 disabled 语义对齐原 fake registry）
- [x] 1.3 新增 `tests/support/__init__.py`（使 support 成为可导入包，按现有 tests 目录约定）

## 2. 改写测试引用 → 指向 `tests/support`（保持全绿，此时 src fake 仍在）

- [x] 2.1 `tests/integration/test_composition.py`：2 个 offline 测试改用 `build_deterministic_coordinator`
     （重命名意图为 deterministic double）；`test_production_composition_rejects_incomplete` 原样保留
- [x] 2.2 `tests/e2e/test_public_boundary_scenarios.py`：`build_offline_coordinator` → `build_deterministic_coordinator`；
      `service._coordinator = coord` → 经 `OrchestrationService(..., coordinator=coord)` 注入（2 处，:72 / :104）
- [x] 2.3 `tests/e2e/conftest.py`：`service` fixture 注入确定性 coordinator；`empty_memory_service` 改用真实
      `MemoryRecallAdapter(recall_fn=lambda *_: ())`；移除 `build_fake_registry`/`FakeMemoryAdapter` 导入
- [x] 2.4 `tests/integration/test_adapters.py`：4 个 fake-registry 测试（round-trip / web denied / web allowed /
      capability_doctor）改写为对真实 adapter 经 router/doctor 的验证，使用 `build_test_registry`
- [x] 2.5 跑受影响测试文件，确认仍全绿（验证 src 的 fake/offline 此时仅余无人引用的死代码）

## 3. 删除生产代码 offline/fake（**BREAKING**）

- [x] 3.1 运行 `gitnexus_impact` on `build_offline_coordinator` / `build_fake_registry` /
      `OrchestrationService.__init__`（upstream），确认直接消费者仅剩 tests（已迁走）；记录 blast radius
- [x] 3.2 `orchestration/composition.py`：删除 `_Fake*` 端口、5 个 raw provider 函数、`build_offline_coordinator`；
      更新模块 docstring（去掉 offline/fake 表述）与 `__all__`（移除 `build_offline_coordinator`）
- [x] 3.3 删除整文件 `src/agents_orchestration/adapters/fake.py`
- [x] 3.4 `application/service.py`：移除 `production: bool` 参数、`self._production` 与 offline 分支；`coordinator`
      属性改为「注入优先，否则 `build_production_coordinator_from_settings`」；新增 `coordinator=None` kwarg；
      默认 `capability_registry` 改为空 `CapabilityRegistry()`；移除 `build_fake_registry` 导入
- [x] 3.5 `cli.py`：`OrchestrationService(backend, production=True)` → `OrchestrationService(backend)`
- [x] 3.6 `adapters/__init__.py`：docstring 去掉「Fake」字样
- [x] 3.7 全仓 grep 确认 `src/` 下无 `build_offline_coordinator`/`build_fake_registry`/`Fake*Adapter`/`_Fake*`/`production=` 残留

## 4. 收尾验证

- [x] 4.1 跑全量测试（默认套件 MUST 无网络调用）确认全绿
- [x] 4.2 `ruff format` + `ruff check` 通过
- [x] 4.3 覆盖率不低于基线（`pytest --cov`，对照变更前）
- [x] 4.4 运行 `gitnexus_detect_changes()` 确认改动只波及预期符号与执行流（offline/fake 装配及其消费者）
- [x] 4.5 架构测试（`tests/test_architecture.py` 等）确认 adapter 边界仍封闭、生产代码无 fake 残留

## 5. 文档

- [x] 5.1 README 补 dev-mode 说明：无 `.env`/LLM key 时 `OrchestrationService` 驱动会在首个 LLM 端口处失败；
      默认测试套件经注入缝运行、无需网络；并承接 `add-orchestration-llm-ports` 5.5 的 deferred Memory/RAG note
