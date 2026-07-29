## Context

`agents_orchestration` 的应用装配根（`orchestration/composition.py`）当前同时维护两条装配路径：

- **offline**（`build_offline_coordinator`）：每个阶段端口都是 `_Fake*`（`_FakeGoalNormalizer`/`_FakePlanner`/
  `_FakeExecutor`/`_FakeAnalyst`/`_FakeWriter`/`_FakeReviewer`）+ raw provider 函数；纯内存、零网络、确定性。
- **production**（`build_production_coordinator_from_settings` → `build_production_coordinator`）：经
  `OpenAIModelAdapter` 接真实 LLM，装配 5 个 LLM 端口 + Research LLM-provider + 持久化 evidence provider。

`OrchestrationService` 用 `production: bool` 开关在二者间分流；`coordinator` 属性懒加载。CLI（`cli.py:29`）永远
`production=True`——**产品入口已经只有一条生产装配**，offline/fake 实际仅服务于测试。

另有第二层 fake：`adapters/fake.py` 的 `FakeMemory/RAG/Web/ModelAdapter` + `build_fake_registry()`，被
`service.py:114` 当作默认 `capability_registry`——**即使 production 模式也挂载**，导致 `capability list` 误报 fake。

约束：本仓库以本地开发为主（CLAUDE.md：分支不推送远端、无外部 review/消费者），可安全做 BREAKING 删除。
`build_production_coordinator`（task 9.4）本就是接受全部端口参数的显式装配函数，是天然的测试注入缝。
真实 adapter（`MemoryRecallAdapter`/`RagAdapter`/`WebResearchAdapter`，task 7.2/7.4/7.6）本就接受
`recall_fn`/`query_fn`/`fetch_fn`，是另一条注入缝。生产路径当前只接 Model，memory/rag/web 真实 adapter 悬空（保持现状）。

## Goals / Non-Goals

**Goals:**

- 生产代码（`src/`）只保留接真实 Adapter 的生产装配，MUST NOT 包含任何 offline/fake 模拟装配或模拟后端。
- 确定性测试能力完整保留——经 `build_production_coordinator` 显式端口缝 + 真实 adapter 注入缝，下沉到 `tests/`。
- 消除 `OrchestrationService` 的 production/offline 开关，并修掉 production 下 `capability list` 误报 fake 的 latent bug。
- 全量测试保持绿、无网络依赖（默认套件）、覆盖率不回退。

**Non-Goals:**

- 不补齐 memory/rag/web 真实 adapter 到生产装配的接线（悬空保持现状，留独立 change）。
- 不改生产装配的 LLM 端口逻辑、prompt、function-calling schema（属 `add-orchestration-llm-ports`）。
- 不动持久化、状态机、Gate、capability 路由策略等领域行为。
- 不引入新的 mock/test-double 框架——只用既有注入缝。
- 不做运行时数据迁移（纯代码重构）。

## Decisions

### D1. 删除 offline 装配，不在生产代码保留任何「dev 模式」fake
**Why**：用户明确只要生产级应用；offline 仅测试用，却编进生产包，与产品定位冲突。
**Alt considered**：保留 offline 作「本地 dev 模式」——否决，会重新把 fake 引回生产代码，违背诉求。

### D2. 测试 double 下沉到 `tests/support/`，复用 `build_production_coordinator` 注入缝
**Why**：`build_production_coordinator(backend, executor=..., normalizer=..., ...)`（task 9.4）已是显式端口装配；
`build_offline_coordinator` 只是「它 + `_Fake*` 端口」的便利封装。下沉后：测试仍确定性，生产代码零 fake，
且测试验证的就是真实装配根的接线契约（比验证一个平行 fake 装配更接近真相）。
**Alt considered**：① 测试全转真实 LLM 调用——否决（flaky、慢、烧 token、需 key，CI 不可复现）；
② 把 `build_offline_coordinator` 留在 src 但标 deprecated——否决（仍违反「无 fake」）。

### D3. `build_fake_registry` 用真实 adapter + 确定性注入函数替代（`build_test_registry`）
**Why**：`MemoryRecallAdapter(recall_fn=...)`/`RagAdapter(query_fn=...)`/`WebResearchAdapter(fetch_fn=...)`
本就是 task 7.2/7.4/7.6 设计的测试缝；用确定性 lambda 即可重建等价 registry，**零 Fake 类存活**，且测试覆盖的是真实
adapter 的 router/doctor 行为（比测 fake 更有价值）。
**Alt considered**：把 `Fake*Adapter` 类整体搬到 tests——否决（多余，且保留「Fake」概念与诉求相悖）。

### D4. `OrchestrationService` 加 `coordinator=None` 注入缝，移除 `production` 开关
**Why**：测试需注入确定性 coordinator；当前靠 `service._coordinator = coord`（`test_public_boundary_scenarios.py:72,104`）
私有属性 hack。正式 `coordinator=None` kwarg 更干净，且让 `coordinator` 属性语义单一：有注入则用，否则建生产装配。
**Alt considered**：`coordinator_factory: Callable`——否决，instance 注入更简单、匹配现有 lazy 属性，测试已持有 backend。

### D5. 默认 `capability_registry` 改为空 `CapabilityRegistry()`
**Why**：生产 coordinator 自建空 registry（`composition.py:396`），service 的 registry 仅服务 `capability list`/
`doctor` 与 legacy `DefaultWorkerHandler`；memory/rag/web 悬空下无可注册项，空默认是诚实终态，并修掉误报 fake 的
latent bug。
**Alt considered**：让 CLI 构造真实 registry——否决（无真实 adapter 可注册，徒增复杂度；与生产 coordinator 的空 registry 对齐即可）。

## Risks / Trade-offs

- **[本地 dev 体验回退]** 无 `.env`/LLM key 时，`OrchestrationService.drive_*` 会在首个 LLM 端口调用处失败。
  → 缓解：README 补 dev-mode 说明；默认测试套件经注入缝运行，不受影响。
- **[受影响测试面较大]** 5 个测试文件依赖 offline/fake。
  → 缓解：先下沉 double（`tests/support`）保持语义等价，再逐文件改写引用，最后删 src；每步跑全量测试。
- **[BREAKING 删除]** 直接 import `build_offline_coordinator`/`build_fake_registry`/`Fake*Adapter` 会断。
  → 缓解：本仓库本地为主、无外部消费者（CLAUDE.md）；变更名与 commit message 显式标注 BREAKING。
- **[与在途 `add-orchestration-llm-ports` 并行]** 该 change 剩 5.3/5.5 两 task。
  → 缓解：已确认均为验证/文档类，不触及本次删除点；5.5 的「deferred Memory/RAG note」可由本次 README 更新承接。

## Migration Plan

纯代码重构，无运行时数据迁移。实施顺序（保证每步可独立验证）：

1. 新增 `tests/support/deterministic.py`（搬入 `_Fake*` + `build_deterministic_coordinator`）与
   `tests/support/test_registry.py`（`build_test_registry`）。
2. 改写 5 个测试文件引用 → 指向 tests/support，并把 `service._coordinator=` 换成 `coordinator=` 注入。
3. 跑全量测试确认仍绿（此时 src 的 fake/offline 尚在，仅测试不再依赖）。
4. 删除 `src` 的 offline/fake：`composition.py` offline 段、`adapters/fake.py`、`service.py` 开关、`cli.py` 标志。
5. 再次跑全量测试 + ruff + 覆盖率，确认绿且无回退。

**回滚**：本地 git revert（分支不推送）。

## Open Questions

- README dev-mode 说明的具体措辞与位置——实施阶段（task）定稿。
