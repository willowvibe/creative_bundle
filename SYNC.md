# Syncing `creative_bundle`

`creative_bundle` is a private, combined snapshot of five repos:

| Source | Bundle path |
|---|---|
| `creative_repos/video-editor` | `video-editor/` |
| `creative_repos/video-projects` | `video-projects/` |
| `creative_repos/vocal-to-song` | `vocal-to-song/` |
| `creative_repos/social-engine` | `social-engine/` |
| `creative_repos/drama-studio` | `drama-studio/` |

The source repos are canonical and stay in
`/mnt/data2/git_repos/creative_repos/`. This repository carries their **working
trees**, not their git histories.

## When

Sync only on a **major change** worth sharing — a new pipeline, a substantial
skill/docs update, a setup or API change. Small fixes can accumulate; the
snapshot does not need to match every commit.

## What is refreshed

- every **tracked** file and symlink in the five source repos
  (`git ls-files`), which includes the tracked `.env.example` and
  `.mcp.json.example` files
- `video-editor/.remember/` (gitignored in the source repo)
- the sibling symlinks `video-editor/videos`, `video-editor/shorts` and
  `drama-studio/videos`

Not refreshed — these are curated in the bundle by hand:

- `claude/` (user-level skills, memory archive, docs index, plugin list)
- `CLAUDE.md`, `README.md`, `SETUP.md`, `SYNC.md`

Never copied: `.env` files, OAuth secrets, credentials, `.venv/`,
`node_modules/`, caches, local assets and renders.

## Procedure

### 1. Commit the source change first

The five source repos have no git remotes; commit locally in whichever repo you
changed, so the snapshot has a stable state to copy.

```bash
cd /mnt/data2/git_repos/creative_repos/<repo>
git status
git add -A && git commit -m "..."
```

### 2. Refresh the five repo folders

Reference script (bash + GNU coreutils; review before running):

```bash
set -euo pipefail

SRC=/mnt/data2/git_repos/creative_repos
DST=/mnt/data2/git_repos/creative_bundle
REPOS="video-editor video-projects vocal-to-song social-engine drama-studio"

# start from the pushed state
git -C "$DST" pull --ff-only

# copy tracked files and symlinks only
for r in $REPOS; do
  while IFS= read -r -d '' f; do
    # points outside the bundle; deliberately excluded
    case "$f" in .claude/skills/tool-trial) continue ;; esac
    [ -e "$SRC/$r/$f" ] || [ -L "$SRC/$r/$f" ] || continue
    mkdir -p "$DST/$r/$(dirname "$f")"
    cp -a "$SRC/$r/$f" "$DST/$r/$f"
  done < <(git -C "$SRC/$r" ls-files -z)
done

# sibling symlinks are untracked in their repos
ln -sfn ../video-projects/videos "$DST/video-editor/videos"
ln -sfn ../video-projects/shorts "$DST/video-editor/shorts"
ln -sfn ../video-projects/videos "$DST/drama-studio/videos"

# session memory is gitignored in the source repo
rsync -a --delete "$SRC/video-editor/.remember/" "$DST/video-editor/.remember/"

# stage deletions/new files, then force-add the tracked set so the repos'
# own .gitignore rules (media, symlinks) do not hide it
git -C "$DST" add -A
for r in $REPOS; do
  while IFS= read -r -d '' f; do
    git -C "$DST" add -f -- "$r/$f"
  done < <(git -C "$SRC/$r" ls-files -z)
done
git -C "$DST" add -f video-editor/.remember \
  video-editor/videos video-editor/shorts drama-studio/videos
```

The copy is additive: a file deleted in a source repo is staged as deleted by
`git add -A` only if it is already tracked in the bundle. Check `git status`
for stale paths if a source repo did a rename or removal.

### 3. Review

```bash
cd /mnt/data2/git_repos/creative_bundle

git status --short
git diff --cached --stat

# no real env files, secrets or credentials
git diff --cached --name-only \
  | grep -E '(^|/)\.env$|(^|/)client_secret|(^|/)credentials' \
  && echo "STOP: remove the secret before committing" || true

# optional parity check; expected extras in the bundle are
# .remember/*, the three videos/shorts symlinks, and the absent tool-trial link
for r in video-editor video-projects vocal-to-song social-engine drama-studio; do
  echo "== $r"
  comm -3 \
    <(git -C "/mnt/data2/git_repos/creative_repos/$r" ls-files | sed "s|^|$r/|" | sort) \
    <(git ls-files "$r" | sort) | head -30
done
```

### 4. Commit and push

```bash
cd /mnt/data2/git_repos/creative_bundle
git commit -m "Sync creative bundle from source repos"
git push origin main
```
