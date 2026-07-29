<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **agents_glm** (10895 symbols, 18352 relationships, 138 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

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

# GitNexus Worktree and Branch Review Rules

## Canonical Index

- Maintain exactly one canonical GitNexus index for this repository at the main repository root.
- NEVER run `gitnexus analyze`, `npx gitnexus analyze`, or
  `node .gitnexus/run.cjs analyze` from a linked, temporary, or agent-created
  worktree.
- Before analyzing, compare `git rev-parse --git-dir` with
  `git rev-parse --git-common-dir`. If their resolved paths differ, the current
  directory is a linked worktree and analysis MUST stop.
- Use the same GitNexus runner/version that created the canonical index. Do not
  mix a global or `npx` GitNexus version with a project-pinned runner against the
  same index.

## Branch and Worktree Review

- Review branch content with the raw Git comparison
  `git diff <base>...<head>` from the target worktree.
- Use the canonical root index for `gitnexus_impact`, `gitnexus_context`, process
  lookup, and test-coverage analysis of changed symbols.
- Do not create a second index merely to review a branch. If an exact
  branch-specific index is exceptionally required, obtain explicit user
  approval, keep it isolated from the canonical index, address it by exact
  absolute path, and unregister it immediately after use.
- If duplicate repository names appear in the GitNexus registry, do not resolve
  them by name. Stop, identify the canonical absolute path, and remove only the
  unintended duplicate registration.
- Changes that `gitnexus analyze` generates in `AGENTS.md`, `CLAUDE.md`, or index
  metadata MUST NOT be included in a branch review diff unless those generated
  files are explicitly part of the requested change.
