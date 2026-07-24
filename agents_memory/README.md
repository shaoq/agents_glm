# agents_memory

独立的 Agent Memory 写入管线：把交互消息转换为有来源、有作用域、有当前有效性的长期记忆。

它与同级子项目完全解耦，拥有自己的依赖、配置、源码、测试、SQLite 真相源和 Chroma
语义索引。设计见 `docs/specs/2026-07-24-memory-write-pipeline-implementation.md`。

## 写入流程

```text
消息 → 事实抽取 → 来源与候选校验 → 批内去重 → Chroma 查找
    → 语义关系判断 → ADD / UPDATE / NOOP → SQLite 提交 → Chroma 同步
```

- `duplicate` → NOOP；
- `supplement` → ADD；
- `contradict` → UPDATE，旧记录 `superseded`；
- `correct` → UPDATE，旧记录 `retracted`；
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
      "content": "请记住，我目前主要使用 Python。"
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

## 查看与删除

```bash
agents-memory list --user-id user-1
agents-memory list --user-id user-1 --history --json-output
agents-memory show MEMORY_ID --user-id user-1
agents-memory delete MEMORY_ID --user-id user-1
```

`list` 默认只返回 `active`。`--history` 同时显示 `superseded` 和 `retracted`。
删除会校验 `user_id`，并同时清理 SQLite 真相和 Chroma 派生索引。

## 索引恢复

```bash
# 重放 pending / failed 的 upsert 与 delete
agents-memory sync repair

# 以 SQLite 中全部 active 记忆重建 Chroma
agents-memory sync rebuild
```

若 SQLite 提交后 Chroma 暂时不可用，写入报告返回 `retryable`，事实不会丢失。默认查询结果
还会回 SQLite 校验，因此脏向量不会被当成当前事实。

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
