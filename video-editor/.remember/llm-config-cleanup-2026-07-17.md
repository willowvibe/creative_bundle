---
name: llm-config-cleanup-2026-07-17
description: "config.yaml llm.tasks block now references only models in the live ollama list; new ModelRouter soft warning for missing models."
metadata:
  type: project
---

The repo's `ollama list` shows: `kimi-k2.7-code:cloud`, `deepseek-v4-pro:cloud`, `glm-5.2:cloud`, `minimax-m3:cloud`, `qwen3-coder-next:cloud`, `gemini-3-flash-preview:latest`. The previous `config.yaml` referenced stale names (`kimi-k2.6:cloud`, `qwen3.6:cloud`, `glm-5.1:cloud`, `gemma4:31b:cloud`) that don't exist on this Ollama Cloud subscription.

- `plan`, `decompose`, `edit_plan`, `scene_plan` tasks now use `minimax-m3:cloud` (same provider family as the cheap PoYo music model for style consistency).
- `code` task uses `qwen3-coder-next:cloud`.
- `fallback` task uses `glm-5.2:cloud`.
- `utility` and `verify_judge` tasks use `gemini-3-flash-preview:latest`.
- `ModelRouter._validate_model_known()` is a soft warning only (logs at `warning` level, never raises) so a missing model in a future install doesn't break the build.
- `docs/MODEL_ROUTING.md` updated to match.

Triggered by: the storyteller templates work, which needed a stable `scene_plan` LLM that exists in the live subscription.

Related: [[storyteller-templates-2026-07-17]].