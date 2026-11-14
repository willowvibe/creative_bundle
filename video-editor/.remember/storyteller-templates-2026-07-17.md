---
name: storyteller-templates-2026-07-17
description: "Added three new video templates (storyteller_budget, storyteller_hd, storyteller_premium) that turn persona-tagged markdown scripts into multi-voice multilingual story videos with FableCut timeline preview and final cut."
metadata:
  type: project
---

Three new `Template` entries share a new `pipeline_kind: "storyteller"` and a new `storyteller_planner` module at `apps/engine/src/video_editor_engine/planner/storyteller_planner.py`. Each template maps to a `(image_tier, video_tier)` pair from a 3×3 tier matrix; `--image-tier` and `--video-tier` CLI flags override the preset's defaults independently. All three consume markdown scripts with YAML frontmatter (`cast:`, `language:`, `music_mood:`) and `## Speaker: <name>` sections, support per-scene `> visual/sfx/language/motion:` blockquote directives, and route to the existing FableCut bridge (`ve storyteller-export`) for browser preview, then `ve render-fablecut` for the final cut.

- `storyteller_budget`  = (`flux-schnell`, `wan-2-2-fast`) — LLM picks ~50% motion
- `storyteller_hd`      = (`gpt-image-2`, `seedance-2-fast`) — user marks `> motion: true`
- `storyteller_premium` = (`gpt-image-2` 2K, `seedance-2`) — hero reels
- 14 templates total now; `test_templates.py` count updated
- LLM default swapped from `kimi-k2.6:cloud` to `minimax-m3:cloud`
- See [[poyo-music-and-sarvam-tts-providers-2026-07-17]] for the cost-leverage details
- See [[llm-config-cleanup-2026-07-17]] for the model-list changes
- See [[storyteller-quality-tiers-2026-07-17]] for the full tier matrix and CLI flag semantics