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

## 工作流（OpenSpec 优先）

- **探索 / 分析类请求 → 启动 OpenSpec explore**：当用户输入「explore」「分析」「探讨」「梳理」「研究」等探索 / 需求分析类关键词时，**启动 OpenSpec 的 explore skill**（`openspec-explore` / `opsx:explore`）进行分析，**不要**进入 Claude Code 自带的 plan mode。
- **后续统一走 OpenSpec 工作流**：explore → propose（`openspec-propose` / `opsx:propose`）→ apply（`openspec-apply-change` / `opsx:apply`）。规划与实施都用 OpenSpec，不用 plan mode。
- **⚠️ explore 后必须等用户确认再创建提案**：explore / 分析完成后，**先呈现方案让用户确认或调整**，**不要直接跳到创建 openspec 提案**（`openspec new change` + proposal/design/specs/tasks）。只有当用户明确说「创建提案」「propose」或确认方案后，才执行提案创建。
- **例外**：纯粹的 bug 调试走 `systematic-debugging`、代码理解走 GitNexus，不强制走 OpenSpec——本规则针对「需求探索 / 方案规划」类任务。
- 原因：本项目以 OpenSpec 做 spec-driven 管理（`openspec/` 已建 specs / changes），工作流需与之对齐。

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **agents_glm** (2074 symbols, 3325 relationships, 42 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

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
