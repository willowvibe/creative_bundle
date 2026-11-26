---
name: cheapag-fallback-claude-subagent
description: "When a Cheap Agent (cheapag) delegate FAILs/TIMEOUTs, the fallback is a Claude (Sonnet) subagent; a hook now forces asking the owner first, and the owner answers 'Continue with Claude'"
metadata:
  node_type: memory
  type: feedback
  originSessionId: cbeb8302-ec2f-4327-a2a0-7c4e91e9bb2d
  modified: 2026-11-26T16:58:08.583Z
---

If a cheapag `delegate` job fails (FAIL, PARTIAL, TIMEOUT, turn cap), hand the work to a Claude subagent (Agent tool, model sonnet).

**Why:** The owner said so on 2026-11-25 ("if cheapag is failing work with claude sub-agent") after cheapag failed on video-editor engine work.

**How to apply:**
- Since 2026-11-26 a PreToolUse hook blocks any tool call after a real FAIL until the owner is asked ("continue with Claude, retry the worker on another model, or stop"). Ask that one question in `wrap_up` with "Continue with Claude" as the first button; the owner picked it every time (6× on 2026-11-26). Do not try to get around the hook.
- A FAIL whose only error is `ModuleNotFoundError: PIL` / `video_editor_asset_ingestion` is environmental (cheapag worktrees have no synced `.venv`): `land apply` it and run the tests in the main tree — the hook allows that. Several such jobs had complete, correct code.
- Brief the Sonnet subagent with the worktree path, the worker's `found:` text, and how to test: `cd <worktree> && PYTHONPATH=$(ls -d packages/*/src | tr '\n' ':') /mnt/data2/git_repos/creative_repos/video-editor/.venv/bin/python -m pytest …`. Ask for a frame/screenshot check when the bug is visual.
- Two parallel jobs that both add fields to the same file (e.g. `explainer_script.py`) will conflict on `land apply`; have the subagent hand-merge in the main tree.
- Always look at rendered frames after an engine "fix": a full-frame-land fix silently mis-projected the coastline; tests passed.
- Spending money (TTS, music, renders) still needs the owner's OK. Related: [[deveekitchen-shorts-style]].
