# video-projects

Content made with the `ve` engine (`../video-editor`): explainer scripts, drama episodes, short packs.
Split out of `video-editor` on 2026-11-11 with its history.

| Folder | What |
|---|---|
| `videos/<date>/<slug>/` | One video per folder: `script.md`, notes, prompts. The finished cut is copied here as `<slug>.mp4` (gitignored); job work and older cuts stay under `/mnt/data2/video-editor-data/`. |
| `videos/mantra-rahasyam/` | Drama series bible and episodes (pipeline: `../drama-studio`). |
| `shorts/` | Short and long-form packs (`short.yaml`, `longform.yaml`). |
| `brand/<channel>/` | Reusable channel assets and generators, e.g. `brand/python-decoder/make_bumpers.py` (intro/outro) and upload defaults. |

Channels: Mantra Shakti (devotional, Telugu/Tamil), DeveeKitchen (Telugu food explainers; slate and
trend evidence in `docs/superpowers/specs/2026-11-14-deveekitchen-first-3-videos-design.md` Rev 3 and
`docs/research/2026-11-26-deveekitchen-trend-scan.md`), Python Decoder
(@pythondecoder101, English coding tutorials with the talking avatar; plan in
`docs/superpowers/specs/2026-11-24-pythondecoder-channel-first-3-videos.md`, EP1 in
`videos/2026-11-24/python-315-features/`, recipe `../video-editor/docs/TECH_TUTORIALS.md`).

`video-editor` reaches this content through two symlinks (`videos`, `shorts`), so commands such as
`uv run ve explain videos/2026-11-10/india-geography/script.md` still run from the engine folder.
Job outputs live under `/mnt/data2/video-editor-data/`.
