# agents_memory

独立的 Agent Memory 写入管线：把交互消息转换为有来源、有作用域、有当前有效性的长期记忆。

它与同级子项目完全解耦，拥有自己的依赖、配置、源码、测试、SQLite 真相源和 Chroma
语义索引。设计见 `docs/specs/2026-07-24-memory-write-pipeline-implementation.md`。

## 写入流程

```text
消息 → 事实/事件抽取 → 来源与候选校验 → 待决项静默回查
    → Chroma 查找 → 多维关系判断 → ADD / UPDATE / NOOP / DEFER
    → SQLite 提交 → Chroma 同步
```

- `duplicate` → NOOP；
- `supplement` → ADD；
- `contradict` → UPDATE，旧记录 `superseded`；
- `correct` → UPDATE，旧记录 `retracted`；
- event 先判断 `same_event / different_event / unknown`，再解释语义关系；
- `unknown + contradict` → DEFER，不猜测 ADD 或 UPDATE；
- DEFER 只写入 SQLite 的 `PendingResolution`，不进入 active memory 或 Chroma；
- 后续同 scope 的自然消息会在普通候选决策前静默尝试消解；
- UPDATE 追加新记录并保留历史关系，不原地覆盖；
- SQLite 是唯一真相源，Chroma 是可修复、可重建的派生索引。

## 安装

```bash
conda activate agents_glm
cd agents_memory
pip install -e '.[dev]'
cp .env.example .env
```

至少配置 `LLM_API_KEY`。默认使用 OpenAI 兼容接口，可通过 `.env` 调整 endpoint、抽取模型、
关系模型和 embedding 模型。

## 写入

输入 JSON：

```json
{
  "request_id": "conversation-42-turn-7",
  "scope": {
    "user_id": "user-1",
    "agent_id": "assistant-1",
    "session_id": "session-9"
  },
  "messages": [
    {
      "message_id": "msg-1",
      "role": "user",
      "content": "我明天计划去北京。",
      "occurred_at": "2026-07-24T09:00:00+08:00"
    }
  ]
}
```

从文件写入：

```bash
agents-memory write input.json
agents-memory write input.json --json-output
```

从 stdin 写入：

```bash
agents-memory write --json-output < input.json
```

同一个 `request_id` 必须始终对应相同输入。重复成功请求直接返回已保存的 `WriteReport`；
若 SQLite 已提交而 Chroma 尚未完成，同一请求只重试索引同步。
同一 scope 内的 `message_id` 也必须稳定且不可复用为不同内容，待消解流程用它识别已经处理过的
自然证据；检测到同 ID 不同内容时请求会被拒绝，而不会静默覆盖旧来源。

`occurred_at` 可选，是“明天、昨天、上周”等相对事件时间的参考。缺少该字段时，
系统保留原始时间表达并标记 unresolved，不使用记忆写入时间代替事件时间。

## 查看与删除

```bash
agents-memory list --user-id user-1
agents-memory list --user-id user-1 --history --json-output
agents-memory show MEMORY_ID --user-id user-1
agents-memory delete MEMORY_ID --user-id user-1
```

`list` 默认只返回 `active`。`--history` 同时显示 `superseded` 和 `retracted`。
删除会校验 `user_id`，并同时清理 SQLite 真相和 Chroma 派生索引。

## 待消解项

```bash
# 精确 scope 查看 open pending，输出包含年龄、价值、原因与过期时间
agents-memory pending list --user-id user-1 --agent-id assistant-1 \
  --session-id session-9 --status open --json-output

# 仅执行到期和目标失效检查；没有新证据时不会调用 LLM
agents-memory pending sweep

# 删除指定时间之前已 resolved/expired/obsolete 的终态记录
agents-memory pending cleanup --before 2026-08-01T00:00:00+08:00
```

TTL 可通过 `PENDING_HIGH_TTL_DAYS`、`PENDING_NORMAL_TTL_DAYS` 和
`PENDING_LOW_TTL_DAYS` 配置，默认分别为 30、7、1 天。过期或 obsolete 不会创建记忆，
也不会改变旧记忆。

## 索引恢复

```bash
# 重放 pending / failed 的 upsert 与 delete
agents-memory sync repair

# 以 SQLite 中全部 active 记忆重建 Chroma
agents-memory sync rebuild
```

若 SQLite 提交后 Chroma 暂时不可用，写入报告返回 `retryable`，事实不会丢失。默认查询结果
还会回 SQLite 校验，因此脏向量不会被当成当前事实。

## 召回（Recall）

显式触发的只读取回管线：按当前查询、用户作用域和预算，从已有记忆中组装可追溯、低冗余的上下文。
Recall 不写入、不修改索引状态、不创建待处理项；首期由调用方决定何时召回。

```text
RecallRequest → 意图 → 分层规划/检索 → 资格过滤 → 效用评分
    → 证据解析（时序/关系/冲突）→ 集合选择 → 上下文组装 → RecallResult
```

- 同一 `user_id` 内三层候选通道：当前会话、Agent 历史、用户共享；调用方可关闭较宽通道；
- SQLite 是真相源，Chroma 只产候选；候选回查 SQLite 后才进入评分；
- 硬过滤（用户/授权/有效性/显式时间与类型）不可被高分或 LLM 覆盖；
- 评分分语义、任务贡献、时间、作用域、可信度、命中稳健性与有界 importance，LLM 只复核前部候选；
- 证据优先用持久 `SUPERSEDES`/`CORRECTS` 关系与结构化时间；关键冲突保留双方；
- 充分性 `SUFFICIENT/PARTIAL/CONFLICTED/EMPTY` 与执行状态 `COMPLETE/DEGRADED` 分离。

### 召回命令

```bash
# 人类可读输出
agents-memory recall "用户偏好什么语言" --user-id user-1 \
  --agent-id assistant-1 --session-id session-9

# JSON 输出（含 evidence / context / metadata）
agents-memory recall "上个月去过哪里" --user-id user-1 --json-output

# 时序视角 + 时间区间
agents-memory recall "一月时的地址" --user-id user-1 \
  --temporal interval --time-start 2026-01-01T00:00:00 --time-end 2026-06-01T00:00:00

# 限制类型、证据数与 token 预算
agents-memory recall "项目进度" --user-id user-1 --type fact \
  --max-evidence 5 --max-tokens 2000

# 诊断输出（降级码与诊断说明）
agents-memory recall "..." --user-id user-1 --diagnostic
```

未配置 `LLM_API_KEY` 时既有写入与维护命令照常可用；只有调用 `recall` 才返回未配置错误。

### 召回配置

Recall 默认使用 `MEMORY_RECALL_MODEL`（默认 `glm-4.7-flash`）。通道候选额度、全局候选上限、
LLM 批量复核上限、默认证据数（≤50）与 token 预算（≤8000）均可通过 `.env` 调整，详见 `.env.example`
中 `RECALL_*` 与 `MEMORY_RECALL_MODEL` 配置项。Recall 配置只在构建召回运行时时校验。

## 作用域与来源

- `user_id` 必填，是强制隐私边界；
- 写入查重使用精确 `(user_id, agent_id, session_id, type)`；
- `fact` 与 `event` 不跨类型查重；
- 仅接受 `user_explicit`、`user_confirmed`、`tool_verified`；
- assistant 建议或推断不能被归属为用户事实；
- “可能、考虑、暂时”等不确定性必须保留。

## 测试

默认测试使用 Fake LLM、Fake Embedder 和临时存储，不访问真实网络：

```bash
pytest --cov=agents_memory --cov-report=term-missing --cov-fail-under=80
ruff check src tests
```

真实 API 端到端验证需要显式配置密钥后运行，不属于默认测试套件。
