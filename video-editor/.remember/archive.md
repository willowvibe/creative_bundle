# Archive

## Week of 2026-11-02
Finished Ep1 master (5:19.5, 1080p25, 0.000s A/V lag; 27 cues + 3 beds; −14.9 LUFS/−1.5 dBTP, music −17 dB) after building SFX/score stages; 4×PRO4500 RunPod batch 31/31 clips ~$2.60. Repo audit → `AUDIT_2026-10-27.md`, shipped Phases 0–2 (atomic SQLite claim, lease/requeue, job_id validation, MCP $1 budget, path checks, subprocess timeouts); relocated creative stack under `creative_repos/`, fixing stale paths, ComfyStudio scopes, worktrees, 3 uv venvs. Connected Manifold MCP (113 tools); Sarvam vs Daddy's blind test → `dialogue_check.py` + Ep1 cast.md.

## Week of 2026-10-26
HyperFrames slice 1 on main (51 commits, 2210 tests); audits #274–#318 (4 HIGH incl. #312 Sarvam voice-clone) → Phases 0–2, 2365 tests. Drama tests chose Wan 2.2 + OmniHuman 2.0 lip-sync; feasibility rev 5 Route B (RunPod InfiniteTalk, ~$245/mo). Rebuilt Mantra Rahasyam Ep1 on 6-gate pipeline + Cloudflare review board, 13 ElevenLabs voices, char bibles; lipsync costs InfiniteTalk $6.35 < Kling $13.80 < OmniHuman $19.20. Video-editor audit → Phases 0–5 (~24 commits, 2483 tests), legacy render path retired, ROADMAP_2026-10-28. RTX PRO 6000 SE 2.2–2.4× faster but PRO 4500 cheaper per render-window; APIs beat rented GPUs for image gen.

## Week of 2026-10-19
Closed motion-QA bugs #240–#271; refreshed pricing; shipped explainer_stills (`ve explain`) + image fallback chain; built Saju Video 2; committed Composition IR spec + plan.

## Week of 2026-10-12
Render-graph P1 rewrite (FFmpeg + HyperFrames HTML; Remotion retired); MCP/OTIO bridge; OpenRouter provider, cost-first routing.

## Week of 2026-08-31
Vlogger Tasks 17–21 (shorts_factory, thumbnail, beat_sync, 9 MCP tools); fixed httpx2 shim breaking mcp 2.0.

## Week of 2026-08-03
Pricing registry + BudgetGovernor cost gates; 71s saju promo; FableCut preview pipeline.

## Week of 2026-07-27
Phases F–J: providers, MCP server, SQLite job queue, path sanitization, E2E render test; closed 51-finding audit.

## Week of 2026-07-20
Phases A–C: Kokoro TTS, xfade, YAML templates, scored provider selection, budget governance, SQLite ledger.

## Week of 2026-07-13
Music video finalized; comfystudio/fablecut bridges; PoYo primary visual provider; 20-task storyteller templates.

## Week of 2026-07-06
musical_video template; closed 35-finding audit (CRITICAL #191 path traversal); 213s/20s promos via FFmpeg composer.