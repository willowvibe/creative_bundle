# social-engine: core + YouTube
Spec: docs/specs/2026-11-03-social-engine-youtube-design.md (read it first; it is the source of truth).
Owner's request: "are you able to connect to the youtube and manage the uploads and all details as needed ... add needed skills and tools"; "we can [add] capability to use browser based usecase ... i can partially help in browser when you can['t]"; then "create a separate folder in social-engine for dealing with all social media like insta, facebook, tiktok, youtube". This plan builds the shared core + YouTube only.
Conventions: Python 3.12, uv project, src layout `src/social_engine/`, pydantic v2 models, Typer CLI, ruff + mypy strict-ish, pytest under `tests/` (flat files, fakes in `tests/fakes/`). Node ≥22 ESM for `browser/`, tests with `node --test`.
State root: `social_engine.core.paths.home()` = env `SOCIAL_ENGINE_HOME` or `~/.config/social-engine`; tests always set the env var to a tmp dir. Secret files are written 0600.
Never make anything visible without an explicit confirm flag; never delete anything on a platform; tests never touch the network or a real browser profile.
Port: steps 1-2 start from commit 0264fd6, which imported the video-editor T1/T2 code (061796c, 914a872) under this layout: core manifest, draft, paths, errors, accounts (the T3 start), core/media.py ffprobe helper, platforms/youtube/costs.py, tests. Reshape it; do not rewrite from scratch. Semantics in the port are still video-editor's (youtube.yaml, channel naming, flat manifest, write_back).
Shared names (later steps rely on these exact names):
  core.paths: home(), platform_dir(platform), client_secret_path(platform), token_path(platform, account), account_path(platform, account), chrome_profile_dir(platform, account), logs_dir()
  core.manifest: Manifest (shared fields + optional `youtube: YouTubeSection`), LoadedManifest(manifest, base_dir, path), load_manifest(path), validate(loaded) -> list[Issue], Issue(field, message, severity: "error"|"warning")
  core.publish_log: PublishLog.load(manifest_path), .entry(platform) -> PlatformEntry | None, .record(platform, *, account, id, url, via, action, changed, warnings), .save(); file `publish-log.json` beside the manifest
  core.accounts: Account(platform, name, platform_account_id: str|None, cdp_port: int, api_audited: bool=False), load_account(platform, name), save_account(acc)
  core.errors: SocialError(code, message, next_step); subclasses QuotaExceeded, UploadLimitExceeded, PrivateLocked, AuthExpired, WrongAccount, ThumbnailNotAllowed, BrowserNeedsUser, BrowserUnexpectedScreen, AlreadyPublished
  core.browser: BrowserResult(status: "done"|"ready_to_confirm"|"needs_user"|"error", summary: dict, id: str|None, screenshot: str|None, message: str), browser_call(platform, command, payload, *, timeout) -> BrowserResult
  platforms.youtube.api: YouTubeApi (Protocol), GoogleYouTubeApi(credentials); platforms.youtube.ops: OpResult(action, video_id, changed: list[str], warnings: list[str])

## 1 reshape the ported core
files: src/social_engine/core/paths.py, src/social_engine/core/errors.py, src/social_engine/core/manifest.py, src/social_engine/platforms/youtube/rules.py, tests/test_manifest.py
do: The port in 0264fd6 already holds paths, errors, manifest, media, costs and their tests. Reshape it, do not rewrite it.
  - `core/paths.py`: drop `VE_YOUTUBE_HOME` and `~/.config/video-editor/youtube` for the Shared names `home()`, `platform_dir(platform)`, `client_secret_path(platform)`, `token_path(platform, account)`, `account_path(platform, account)`, `chrome_profile_dir(platform, account)` and `logs_dir()`; root `SOCIAL_ENGINE_HOME` or `~/.config/social-engine`, account names validated so they cannot escape the home.
  - `core/errors.py`: `YouTubeError` becomes `SocialError`; `WrongChannel` → `WrongAccount`, `StudioNeedsUser` → `BrowserNeedsUser`, `StudioUnexpectedScreen` → `BrowserUnexpectedScreen`; add `AlreadyPublished`; messages say `social ...`, not `ve yt`.
  - `core/manifest.py`: split the flat model into shared fields (video, title, description, thumbnail, language, synthetic_media, captions) plus optional `youtube: YouTubeSection` (account, kind, title/description overrides, chapters, tags, category, made_for_kids, privacy, publish_at, localizations, playlists, dubs); `load_manifest` reads `publish.yaml`; delete `write_back` and the video_id/uploaded_* fields; `validate` runs the shared checks (required fields, referenced files exist, BCP-47-like language keys `^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$`) then each present platform's rules.
  - `platforms/youtube/rules.py`: move the YouTube checks, their constants and `render_description` out of `core/manifest.py`, keeping the behaviour the ported tests pin: title ≤100 chars and no `<`/`>`; description ≤5000 UTF-8 bytes after chapters; tags ≤500 chars counting a space as len+2; Shorts vertical and ≤180 s; thumbnail jpg/png ≤2 MB; publish_at future and private only; privacy private/unlisted/public. Chapters come from `chapters.json` (list of `{"start": seconds, "title": str}`, also `{"chapters": [...]}`) as `M:SS Title` lines (`H:MM:SS` past an hour), first at 0:00.
done-when: `uv run pytest tests/test_manifest.py -q`; `uv run ruff check .`; `uv run mypy src`; tests cover a valid publish.yaml, one failing case per rule, chapter rendering under and over one hour, and that no `write_back` or `channel:` key survives
dont: import anything from video-editor; read real files under ~/.config; keep the `youtube.yaml` name
difficulty: medium

## 2 publish log and manifest drafting
files: src/social_engine/core/publish_log.py, src/social_engine/core/draft.py, tests/test_publish_log.py, tests/test_draft.py
needs: 1
do: The drafting heuristics are ported in `core/draft.py` (0264fd6); reshape them and add the log.
  - `core/publish_log.py` per Shared names: JSON file beside the manifest, atomic write (temp file + rename), `record` appends to the entry's history and updates id/url/via/published_at; also `caption_hashes` dict per entry for step 6; a manifest that already has a youtube entry raises AlreadyPublished and points to `update`.
  - `core/draft.py`: `draft_manifest(folder, *, platform="youtube", account, kind=None) -> Path` writes `publish.yaml` (shared fields on top, then a `youtube:` section) and refuses if it exists; drop video_id/uploaded_*.
  - Keep the ported detection rules: video = single `final*.mp4` else the largest `.mp4` (error listing candidates if two are equal size); short when vertical and ≤180 s; thumbnail `thumbnail.(jpg|png)`; chapters `chapters.json`; captions = every `*.srt` whose stem ends with a language code (`te.srt`, `ep1.en.srt`, `subs/hi.srt`); dubs = audio (`.m4a .wav .mp3 .aac`) whose stem ends with a language code other than `language`; language = first caption's code else `en`; title/description/tags empty with `# TODO(owner)`; privacy private; synthetic_media true; made_for_kids false.
done-when: `uv run pytest tests/test_publish_log.py tests/test_draft.py -q`; `uv run ruff check .`; tests use tmp folders shaped like a finished drama episode (final.mp4 via a stubbed probe, te/en/hi/ta srt, hi/ta m4a)
difficulty: easy

## 3 accounts and YouTube OAuth
files: src/social_engine/core/accounts.py, src/social_engine/platforms/youtube/auth.py, tests/test_accounts.py, tests/test_youtube_auth.py
needs: 1
do: `core/accounts.py` is ported as `ChannelProfile` (0264fd6); rename it to `Account` (`platform_account_id`, `cdp_port`, `api_audited`), store YAML at `account_path`, and give a new account the next free `cdp_port` from 9333 upward across all platforms.
  - `platforms/youtube/auth.py`: SCOPES = youtube.upload, youtube.force-ssl, yt-analytics.readonly (full URLs). `authorize(account, *, flow_factory=InstalledAppFlow.from_client_secrets_file) -> Credentials` runs `run_local_server(port=0)`, writes the token JSON 0600 at `token_path("youtube", account)`, calls `channels.list(mine=True, part="id")` and stores the id as `platform_account_id`; a different stored id raises WrongAccount and leaves files as they were.
  - `load_credentials(account) -> Credentials` refreshes expired tokens and rewrites the file; RefreshError → AuthExpired; missing client_secret.json → SocialError whose next_step quotes the spec's Owner setup step 1.
done-when: `uv run pytest tests/test_youtube_auth.py -q`; `uv run mypy src`; tests use a fake flow and fake credentials: token mode 0600, id stored, mismatch raises WrongAccount, refresh rewrites, RefreshError maps, missing secret message
difficulty: medium

## 4 YouTube API client and fake
files: src/social_engine/platforms/youtube/api.py, tests/fakes/__init__.py, tests/fakes/youtube_api.py, tests/test_youtube_api.py
needs: 3
do: `YouTubeApi` Protocol with insert_video(body, media_path, on_progress), update_video(body, parts), get_video(video_id, parts), set_thumbnail(video_id, path), list_captions(video_id), insert_caption(video_id, language, name, path), update_caption(caption_id, path), list_playlists(), insert_playlist(title, privacy), list_playlist_items(playlist_id), insert_playlist_item(playlist_id, video_id), list_my_videos(limit), my_channel_id(), analytics_report(video_id, start, end, metrics).
  - `GoogleYouTubeApi(credentials)` builds `youtube` v3 and `youtubeAnalytics` v2 with cache_discovery=False; uploads use MediaFileUpload(resumable=True, chunksize=8 MiB) with `next_chunk()` and backoff 1,2,4,8,16 s (max 5 retries) on HTTP 500/502/503/504 and connection errors.
  - `map_http_error(err) -> SocialError`: quotaExceeded, uploadLimitExceeded, 401/authError → AuthExpired, thumbnails 403 forbidden → ThumbnailNotAllowed (next_step: verify the channel by phone at youtube.com/verify); others keep the API message.
  - `supports_synthetic_media() -> bool`: true when the discovery doc's Video.status schema has `containsSyntheticMedia`; cached.
  - `tests/fakes/youtube_api.py`: in-memory FakeYouTubeApi implementing the Protocol with a call log, injectable errors and a `force_private` switch that simulates the unaudited lock.
done-when: `uv run pytest tests/test_youtube_api.py -q`; `uv run mypy src`; tests cover backoff retry-then-success, each mapped error, synthetic-media detection true/false
difficulty: medium

## 5 YouTube upload and update
files: src/social_engine/platforms/youtube/ops.py, tests/test_youtube_ops_upload.py
needs: 2, 4
do: `upload(manifest_path, api, *, confirm) -> OpResult`:
  - load + validate, abort with the issue list on any error-severity issue; AlreadyPublished when the publish log has a youtube entry (message points to `update`); refuse unless confirm is True.
  - body: snippet (title/description with youtube overrides, rendered description, tags, categoryId, defaultLanguage and defaultAudioLanguage = manifest language) and status (privacyStatus, publishAt, selfDeclaredMadeForKids, containsSyntheticMedia only when supported, else a warning that the disclosure must be set in Studio).
  - after insert, `get_video`; requested unlisted/public but returned private → PrivateLocked warning (not an exception); thumbnail set when given, ThumbnailNotAllowed becomes a warning; record in the publish log with via="api" and the watch URL.
  - `update(manifest_path, api, *, confirm) -> OpResult`: needs a youtube log entry; fetch current snippet/status, send only parts that differ, return the changed field names; confirm required only when privacy or publish_at changes.
done-when: `uv run pytest tests/test_youtube_ops_upload.py -q`; `uv run mypy src`; tests with FakeYouTubeApi: happy path call order insert→get→thumbnail and log written, no-confirm refusal, AlreadyPublished, validation abort, private-lock warning, thumbnail warning, update no-op, update privacy change needs confirm
difficulty: medium

## 6 YouTube captions, localizations, playlists, list and stats
files: src/social_engine/platforms/youtube/ops.py, tests/test_youtube_ops_sync.py
needs: 5
do: Add to ops.py:
  - `sync_captions`: per manifest language insert when absent, update media when the SRT sha256 differs from the publish log's `caption_hashes`, skip otherwise; tracks not in the manifest are reported as warnings, never deleted.
  - `sync_localizations`: set snippet.defaultLanguage when missing and the localizations part; skip when unchanged.
  - `sync_playlists`: find by exact title, create as private when missing, add the video unless already an item.
  - `list_videos(api, limit) -> list[VideoSummary]` newest first (id, title, privacy, published_at, views); `stats(api, video_id, days) -> VideoStats` via analytics (views, estimatedMinutesWatched, averageViewDuration, averageViewPercentage, likes, subscribersGained for the window) plus lifetime counts from videos.list statistics. No CTR field; the docstring says CTR is read in Studio.
done-when: `uv run pytest tests/test_youtube_ops_sync.py -q`; `uv run mypy src`; tests cover caption insert/update/skip/extra-track warning, localization skip, playlist create+add+already-present, list ordering, stats shape
difficulty: medium

## 7 browser driver core
files: package.json, package-lock.json, browser/driver.mjs, browser/lib/chrome.mjs, browser/lib/steps.mjs, browser/test/steps.test.mjs, browser/test/fixtures/
deps: playwright-core@1.63.0
do: Root `package.json` (private, type module, engines node ≥22) with playwright-core pinned exactly 1.63.0 and script `"test:browser": "node --test browser/test"`.
  - `lib/chrome.mjs`: `ensureChrome({profileDir, port})` starts system Chrome (`google-chrome` on Linux, standard macOS/Windows paths) detached with --user-data-dir, --remote-debugging-port, --no-first-run when nothing listens on the port; `attach(port, startUrl)` uses chromium.connectOverCDP and returns a page on startUrl (reusing a tab already there).
  - `lib/steps.mjs`: `expectScreen(page, name, checks, {timeout})` with role/label/text locators only; `detectHandoff(page)` returns a reason for accounts.google.com URLs, CAPTCHA, "Verify it's you", or an account chooser; `stopUnexpected(page, step)` and `stopForUser(page, reason)` save a full-page screenshot under `logs/` and return the error / needs_user result object.
  - `driver.mjs`: `node browser/driver.mjs <platform> <command> --payload <file>` prints exactly one JSON object (BrowserResult shape); dispatches to `browser/platforms/<platform>/index.mjs`; generic commands `login` (ensureChrome + attach; needs_user until the platform's `isSignedIn(page)` is true, then done with the account id the platform reads) and `status`.
  - Fixtures: simple signin, dashboard and unknown-screen HTML pages; tests load them with page.setContent in headless Chromium from playwright-core.
done-when: `npm install`; `npm run test:browser`; tests cover expectScreen pass/fail, each detectHandoff reason, screenshot written on stop
dont: select by CSS class names; add other npm packages
difficulty: hard

## 8 YouTube Studio flows
files: browser/platforms/youtube/, browser/test/youtube.test.mjs, browser/test/fixtures/youtube/
needs: 7
do: `browser/platforms/youtube/index.mjs` exports isSignedIn (Studio dashboard visible; channel id from `/channel/<id>` in the URL) and commands:
  - `upload` (payload: resolved fields + absolute paths): Create → Upload videos → file input → Details (title, description, thumbnail input, playlist checkboxes by visible name, not made for kids, Show more → altered content yes/no from synthetic_media, video language) → Next to Visibility → private/unlisted/public or Schedule with publish_at → read the video link → return ready_to_confirm with summary and id. Never clicks the final button.
  - `confirm` (payload `{id, expect: "publish"|"dubs"}`): attach, check the open dialog belongs to that id, click the final Save/Publish/Schedule, wait for the done state, return done.
  - `add-dubs` (payload id + dubs map): open `studio.youtube.com/video/<id>/translations`, add each missing language, Add dub, upload the audio, return ready_to_confirm.
  - Every transition calls expectScreen; every wait checks detectHandoff first. Fixtures are simplified HTML with the labels and roles the flows use; tests prove step order and that `upload` never clicks the final button. A header comment records that labels must be re-checked against live Studio (step 13).
done-when: `npm run test:browser`
dont: select by CSS class names
difficulty: hard

## 9 Python bridge to the browser driver
files: src/social_engine/core/browser.py, src/social_engine/platforms/youtube/browser_ops.py, tests/test_browser_bridge.py
needs: 3, 8
do: `core/browser.py` `browser_call` per Shared names: payload to a temp JSON file, run `node <repo>/browser/driver.mjs <platform> <command> --payload <file>` with subprocess.run(timeout=…), parse the single stdout JSON into BrowserResult; needs_user → BrowserNeedsUser and error → BrowserUnexpectedScreen (both carry the screenshot path); non-JSON stdout or non-zero exit → SocialError with the stderr tail. Every payload gets the account's cdp_port and chrome_profile_dir.
  - `platforms/youtube/browser_ops.py`: `browser_upload(manifest_path) -> BrowserResult` (validate, AlreadyPublished check, send `upload`, return the summary) and `browser_confirm(manifest_path, video_id, expect)` (send `confirm`; only after `done` record the publish log with via="browser"); `browser_dubs(manifest_path)` the same two-call way.
done-when: `uv run pytest tests/test_browser_bridge.py -q`; `uv run mypy src`; tests patch subprocess.run: JSON parsing, each status mapping, log written only after confirm, bad stdout error
difficulty: medium

## 10 social CLI
files: src/social_engine/cli.py, tests/test_cli.py
needs: 6, 9
do: Typer app `app` (the console script from step 1):
  - `login <platform> <account>`, `auth <platform> <account>`, `init <folder> --account [--kind]`, `validate <manifest>` (issues + quota estimate, exit 1 on errors), `publish <manifest> [--platform youtube] [--via browser|api] [--yes]`, `update <manifest> [--yes]`, `list <platform> <account> [--limit 20]`, `stats <manifest> [--days 28]`, sub-app `yt` with `captions`, `localize`, `playlist`, `dubs [--yes]`.
  - default via for YouTube is browser unless the account has api_audited true; without --yes, publish/update/dubs print the summary and exit 0 without acting; the browser lane prints the ready_to_confirm summary and asks `Publish now? [y/N]` unless --yes.
  - exit codes: BrowserNeedsUser prints the message + screenshot path and exits 3; other SocialError prints `code: message` and `next: next_step` and exits 2.
done-when: `uv run pytest tests/test_cli.py -q`; `uv run ruff check .`; tests use CliRunner with patched ops/bridge: default via, --yes gating, exit codes
difficulty: medium

## 11 MCP server
files: src/social_engine/mcp_server.py, pyproject.toml, tests/test_mcp_server.py
needs: 6, 9
do: stdio MCP server with the `mcp` SDK, console script `social-engine-mcp`. Tools social_validate {manifest}, social_publish {manifest, platform?, via?, confirm}, social_update {manifest, confirm?}, social_list {platform, account, limit?}, social_stats {manifest, days?}.
  - social_publish and social_update refuse unless `confirm` is literally true; via browser, the first call returns ready_to_confirm and a second call with `confirm: true` and the returned id publishes.
  - manifest paths must resolve (after symlinks) inside the allowed roots from env `SOCIAL_ENGINE_ROOTS` (colon-separated), default `/mnt/data2/git_repos/creative_repos/video-editor/videos`; anything else is refused.
done-when: `uv run pytest tests/test_mcp_server.py -q`; `uv run ruff check .`; tests: tool list, confirm guard, browser two-call flow with patched bridge, path outside roots refused, symlink escape refused
difficulty: medium

## 12 skill and docs
files: .claude/skills/social-publish/SKILL.md, README.md, docs/usage.md, .mcp.json.example
needs: 10, 11
do: Skill `social-publish`: when to use; YouTube owner setup copied from the spec; lane table; the confirmation rule (show the summary, wait for the owner's yes in chat, one approval per action); hand-off protocol (exit 3 / needs_user: tell the owner what is on screen and which Chrome window, wait, rerun the same command); drama order (init → owner edits text → validate → publish private → captions → localize → playlist → dubs → owner approves visibility); CTR lives in Studio; no deletes.
  - `docs/usage.md`: every CLI command with one example; `.mcp.json.example` registering `social-engine-mcp`; README links both.
done-when: `test -f .claude/skills/social-publish/SKILL.md`; `test -f docs/usage.md`
difficulty: easy

## 13 live verification with the owner
files: logs/
needs: 12
do: Not for a worker: done in the main session with the owner. Owner completes Google Cloud setup, `social auth youtube <account>` and `social login youtube <account>`. Make a 5-second test clip with a manifest (private): validate → publish --via api → yt captions → yt playlist → stats; then publish --via browser, stop at ready_to_confirm, confirm, check publish-log.json. Explore live Studio with `agent-browser` and correct any fixture labels that differ, rerun `npm run test:browser`. Owner removes the test videos in Studio.
done-when: owner confirms both lanes worked on their channel
difficulty: medium

## status (2026-11-03)
Steps 1-12 are implemented (`46a0f03` … `9a676dd`, live fixes in `843e5dc`).
Step 13 was run on `mantra-shakti`:

- OAuth and browser login worked. The browser lane reached `ready_to_confirm` and saved a private draft with the live AI-use disclosure; the flow's live locators were corrected and `npm run test:browser` passes.
- The API lane is blocked: the account manages the channel as a Brand Account manager added through YouTube Studio permissions, which the Data API does not recognise. The owner's token or legacy Brand Account permissions would unblock it.
- The owner removed the test drafts; local test artifacts and the old wrong-channel tokens were removed.
- Remaining optional work: browser flows for captions/playlists, and the API lane once the owner provides access. Details: `docs/live-verification.md`.
