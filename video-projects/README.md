# video-projects

Content made with the `ve` engine (`../video-editor`): explainer scripts, drama episodes, short packs.
Split out of `video-editor` on 2026-11-11 with its history.

| Folder | What |
|---|---|
| `videos/<date>/<slug>/` | One video per folder: `script.md`, notes, prompts. Renders and caches stay on disk, untracked. |
| `videos/mantra-rahasyam/` | Drama series bible and episodes (pipeline: `../drama-studio`). |
| `shorts/` | Short and long-form packs (`short.yaml`, `longform.yaml`). |

`video-editor` reaches this content through two symlinks (`videos`, `shorts`), so commands such as
`uv run ve explain videos/2026-11-10/india-geography/script.md` still run from the engine folder.
Job outputs live under `/mnt/data2/video-editor-data/`.
