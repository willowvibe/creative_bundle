---
name: delegating-to-opencode
description: Use when a task could go to a sub-agent - a code change, a fix with tests, a broad search, a review or a draft - and the user prefers opencode with Ollama cloud models over Claude subagents, Cheap Agent or doing it inline. Also use when an `opencode run` call was blocked by auto mode, when opencode ignored a permission config, or when choosing an `ollama/...:cloud` model.
---

# Delegating to opencode (Ollama cloud models)

## Overview
The user wants opencode as the default sub-agent, running their Ollama cloud models. Claude
writes the brief, opencode works inside an enforced permission set, and Claude reviews the
result before reporting or committing. A sub-agent's report is a claim, not proof.

## When to use
- Self-contained code changes with a test command, bug fixes, refactors in named files
- Read-only sweeps and review rounds: searching a repo, summarising files, reviewing a diff (`-r`)
- Not for: work that needs this conversation's live context mid-task, network, secrets,
  pushes, paid APIs, or a one-line edit that is faster done inline

## Models (opencode ids; the user's `ollama list`)
| Model | Use for |
|---|---|
| `ollama/deepseek-v4.1-flash:1017-cloud` | Default implementer for code changes |
| `ollama/glm-5.3-flash:cloud` | Fallback when a run fails or comes back weak; independent reviewer |
| `ollama/minimax-m3.1-flash:cloud` | Fast read-only sweeps, summaries, probes |

All three have tools and a 1M context. `ollama-cloud/*` ids also work, but the user chose these
three. If a model errors before doing any work, retry once on the next model in the table.

## How to run
```bash
~/.claude/skills/delegating-to-opencode/oc-delegate.sh -t "title" -s ses_<task_slug> BRIEF.md LOG.txt
#  -m <model>   -r read-only   -a 'extra shell pattern'   -s ses_<slug>   OC_TIMEOUT=seconds
```
Run it from the repo root with `run_in_background: true`, and wait for the notification. Keep
the brief and log in the scratchpad.

What the script enforces (via `--standalone` + `OPENCODE_CONFIG_CONTENT`):
- reads and edits only inside the current directory (no edits with `-r`); `.env` files denied.
  This matters because everything a tool reads is sent to the cloud model
- shell: `uv run pytest/ruff/mypy`, read-only git, `ls`, `wc`, plus any `-a` patterns; every
  other command is denied, including each part of a chained command
- no webfetch, websearch or nested subagents
- everything else denied by default, which removes the JavaScript `execute` tool and every MCP
  server from the global config (playwright, chrome-devtools, replicate, elevenlabs, …)

Agents (`-g`, from the opencode-power-pack plugin, installed): `code-reviewer` (diff review),
`code-explorer` (trace a feature), `code-architect` (design a change). The plugin keeps all three
read-only even when the wrapper allows edits, and they cannot run tests, so their findings come
from reading the code.

## Brief recipe (what every brief contains, in order)
1. Repo path and how to run tools (`uv run …`)
2. The bug or goal, with a runnable reproduction and its current wrong output
3. The required behaviour as checkable facts (exact values, and edge cases that must stay unchanged)
4. Process: failing tests first, run them red, then the minimal change, then green
5. The exact verify commands
6. Hard limits: the files it may edit; leave other uncommitted changes alone; no state-changing git
7. The report it ends with: what changed, the red and then green counts, any doubts

## Review loop (per task)
1. Scope: `git status --short` and `git diff` on the allowed files only
2. TDD evidence: `grep -nE "failed|passed" LOG` shows red before green
3. Spec check, then quality check: run the power-pack reviewer on a different model from the
   implementer, then read its findings and the diff yourself:
   `oc-delegate.sh -r -g code-reviewer -m ollama/glm-5.3-flash:cloud BRIEF LOG`. Its brief names
   the commit or files, asks for at most 3 findings with file:line and a concrete failing input,
   and says no edits. Reproduce each finding before acting on it
4. Gaps go back to the SAME session (`-s ses_<task_slug>`), naming exactly what to fix; repeat 1–3
5. Rerun the verify commands yourself before you report or commit
6. One task at a time per checkout; parallel tasks need separate worktrees (see the next section)

## Common mistakes
| Mistake | Fix |
|---|---|
| `opencode run --auto` | Blocked by Claude Code auto mode (it approves everything). Use the script |
| Config passed without `--standalone` | The shared background service never sees it, so every action is allowed. The script adds `--standalone` |
| `bash` key, or absolute paths in edit rules (v2) | v2 calls it `shell` (`bash` works as an alias); edit paths match relative to the project |
| `-s my-task` | Session ids must start with `ses_` |
| Trusting exit code 0 | opencode can exit 0 on failure: read the denial and error counts the script prints |
| A `--worktree` run in a uv workspace | Editable installs import the main checkout, so its tests check the wrong code |
| Allow-by-default config with a few denies | The `execute` tool reaches every MCP server (web via Playwright, paid Replicate). Start with `"*":"deny"` and allow by name |
| Scoping `grep`/`glob` by path | Their rules match the search pattern, not the path. Only `external_directory: deny` keeps them in the repo |
| Allowing `find *`, `cat *` or `python -c *` | They write, run or read anything, anywhere. The model has its own read tool; add these per task with `-a` only when needed |
