# drama-studio

The Telugu drama pipeline (Mantra Rahasyam and later series), split out of `video-editor` on 2026-11-11
with its history. Nothing here imports the `ve` engine; it shells out to it where needed.

| Path | What |
|---|---|
| `scripts/drama_review.py` | Stage gates (script → dialogue → voices → images → videos → lipsync → final), contact sheets, storyboard |
| `scripts/drama_board.py` | Publishes a stage to the review board and pulls notes back |
| `scripts/dialogue_check.py` | Sarvam second read of the dialogue (3 runs, majority) |
| `scripts/story_rules.py`, `scripts/episode_state.py` | Story and continuity checks; the episode ledger |
| `scripts/flow_call.py` | Flow MCP helper |
| `apps/drama-review/` | The family review board (Cloudflare Worker + D1) |
| `.claude/skills/drama-episode/` | The production skill; start here |
| `docs/DRAMA_*.md` | Playbook, pipeline, story rules, research |

Local layout (all symlinks, ignored): `.env` → `../video-editor/.env`, `videos` →
`../video-projects/videos` (episode folders).

```bash
uv sync
uv run pytest
```
