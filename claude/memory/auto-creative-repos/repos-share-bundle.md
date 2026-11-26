---
name: repos-share-bundle
description: creative_bundle private repo — five creative repos plus Claude context, at /mnt/data2/git_repos/creative_bundle (created 2026-11-14)
metadata:
  node_type: memory
  type: project
  originSessionId: dd78c013-0f33-4952-be22-c406b8ba4e6b
  modified: 2026-11-26T07:36:17.988Z
---

On 2026-11-14 I built `/mnt/data2/git_repos/repos-share/`: a shareable snapshot of `video-editor`, `video-projects`, `vocal-to-song`, `social-engine` and `drama-studio` (full git history, remotes removed). It restores the sibling layout (`video-editor/videos|shorts` and `drama-studio/videos` → `../video-projects/...`), adds per-repo `.env.example` files (never `.env`), and carries Claude context: root `CLAUDE.md`, `claude/skills/` (48 user-level skills), `claude/memory/`, `claude/docs/INDEX.md`, `claude/PLUGINS.md`. `SETUP.md` is the consolidated new-machine guide (tool installs, per-repo setup, API keys per repo, MCP connections, gotchas); `README.md` links to it.

Excluded: local assets, `.venv`/`node_modules`, `video-editor/.superpowers/`, the local `.mcp.json`, and all settings/credentials. `video-editor/.remember/` memory (41 notes) is included and gitignored. Keep the five repo folders together — the symlinks break if moved individually.

On 2026-11-14 the bundle was imported into the private repo `git@github.com:willowvibe/creative_bundle.git` at `/mnt/data2/git_repos/creative_bundle` (import `81b66a1`, 1837 files; sync doc `d9c6b73`). The import carries working trees only, not the five nested git histories; the original repos remain in `creative_repos/`. The old `repos-share/` folder was deleted after the push.

Sync practice learned 2026-11-26 (syncs `f846564`, `aa02359`): when a source repo has **uncommitted edits the session did not make** (e.g. social-engine's in-progress `mcp_server.py`, an om-saravana `NOTES.md`), ship the file's `HEAD` version (`git show HEAD:<path>`), not the working copy, and tell the owner which files. In SYNC.md's force-add loop, skip `.claude/skills/tool-trial` (it is not in the bundle, and `git add -f` on it aborts the loop). Scan staged content for key patterns as well as file names before pushing.

Sync policy: refresh and push the bundle only after a **major change** to the five source repos, not every commit. Procedure in `creative_bundle/SYNC.md`; workspace notes in `creative_repos/CLAUDE.md` and `/mnt/data2/git_repos/CLAUDE.md`. Neither `/mnt/data2/git_repos` nor `creative_repos` is a git repo, and the source repos have no remotes — `creative_bundle` is the only pushed copy.
