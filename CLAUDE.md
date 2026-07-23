# 项目工作规则

## Git

- **分支不推送远端**：开发 / feature 分支仅在本地累积提交，**默认不** `git push`。
  - 收到 "commit" 指令时只在本地完成提交，不推送远端。
  - `main` 等共享主干分支不在此列。
  - 仅当用户**明确**要求推送时才执行 `git push`。
  - 原因：本仓库以本地开发为主，无需远端协作 / review，避免远端分支堆积。

## 配置（.env）

- **`.env` 必须与 `.env.example` 配置项完全对齐**：即使某项在代码（`config.py` 的 `Settings`）或 `.env.example` 里有默认值，也要在 `.env` 中显式列出——让 `.env` 自包含、可直接编辑，运维 / 切换平台时无需回看 example 或代码默认值。
- 新增 / 修改配置项时，同步更新 `agents_rag/.env` 与 `agents_rag/.env.example` 两处，保持键集合一致。
- 校验：`diff <(grep -oE '^[A-Z_]+' agents_rag/.env | sort) <(grep -oE '^[A-Z_]+' agents_rag/.env.example | sort)`（无输出即一致）。

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **agents_glm** (1980 symbols, 3164 relationships, 39 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/agents_glm/context` | Codebase overview, check index freshness |
| `gitnexus://repo/agents_glm/clusters` | All functional areas |
| `gitnexus://repo/agents_glm/processes` | All execution flows |
| `gitnexus://repo/agents_glm/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
