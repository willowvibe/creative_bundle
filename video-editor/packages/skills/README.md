# video_editor_skills

All video-editor skill modules now ship from this one distribution. The
top-level import names (`rough_cut`, `graphics`, `captions`, `qa`, …) are
unchanged, so callers and the CLI do not need new import paths.

- `src/skills/` — shared skill protocol and base types.
- `src/<skill>/` — one package per skill; its README is in `docs/`.
- Install the optional beat detector with `video-editor-skills[detector]`.

Keeping one `pyproject.toml` replaces 21 single-module workspace members and
their duplicated dependency lists. See A5 in `docs/ISSUES_BUGS.md`.
