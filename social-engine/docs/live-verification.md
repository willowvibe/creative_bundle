# Live verification — 2026-11-03

First real run against a channel, done with the owner. No secrets are recorded here.

## Target

- Account: `mantra-shakti`
- Channel: **Mantra Shakti - The Power of Mantras**
- Channel ID: `UC7R1FrSt8s2YYVcDSbi8hEQ`
- Access: Brand Account **manager**, granted through YouTube Studio permissions
- State home: `~/.config/social-engine/`

## What worked

- The Google OAuth desktop client was stored at
  `~/.config/social-engine/youtube/client_secret.json` (mode 0600).
- `social auth youtube mantra-shakti` completed consent, wrote the token with
  mode 0600, and stored the channel ID. The wrong-channel guard refused a token
  that resolved to a different channel.
- `social login youtube mantra-shakti` confirmed the dedicated Chrome profile was
  signed in as a manager of mantra sakthi and returned channel ID
  `UC7R1FrSt8s2YYVcDSbi8hEQ`.
- The browser upload flow reached `ready_to_confirm` with a private upload, and
  the private draft was saved with the correct title, description and thumbnail.
- The live **AI use → Yes, AI was used** disclosure was set and verified after a
  reload.

## Live Studio corrections made

The fixture labels had to be re-checked against live Studio. The flow now uses:

- contenteditable textboxes named
  `Add a title that describes your video...` and
  `Tell viewers about your video...`
- the **Upload file** button for the thumbnail, not a labelled file input
- the intermediate **Video elements** and **Checks** steps before **Visibility**
- **Show advanced settings → AI use → Yes, AI was used**
- the exact **Next** button (the page also has a carousel “Next item”)
- the upload dialog (`ytcp-uploads-dialog`) as the control scope when present

The Node driver now exits after printing its JSON result instead of staying alive
on the CDP connection.

## Blocker

Manager access granted through **YouTube Studio permissions** is not recognised by
the YouTube Data API. For this account:

- the **browser lane works** for uploads, metadata, thumbnail and dubs;
- the **API lane cannot manage the channel** — captions, localizations,
  playlists and analytics need either:
  - a token from the Brand Account **owner**, or
  - the owner switching the channel to legacy Brand Account permissions and
    re-adding the manager there.

The same limitation makes the API lane unavailable for this channel even after
re-consent; the personal “Harish Reports” channel is a different channel and was
not used for the target test.

## Live behaviour to remember

- Studio can reset the upload dialog when the driver disconnects. Without
  `--yes`, the upload becomes a **private draft**. Use `--yes` for a one-pass
  upload and Save, or finish the draft in Studio.
- The two-call `ready_to_confirm` → `confirm` flow is reliable with the fixture
  but not with the live dialog reset. Treat the staged result as a private draft
  when the dialog is gone.

## Cleanup performed

- The owner deleted the five test drafts created during the attempts.
- Local `/tmp/opencode/social-engine-smoke` artifacts and temporary screenshots
  were removed.
- The two old “Harish Reports” backup tokens were removed.

## Current state

- `client_secret.json` remains under the YouTube state directory.
- No active API token exists for `mantra-shakti`.
- `accounts/mantra-shakti.yaml` targets `UC7R1FrSt8s2YYVcDSbi8hEQ`.
- The dedicated Chrome profile is still signed in as manager of mantra sakthi.

## Next steps

1. To use the API lane on mantra sakthi, have the Brand Account owner provide a
   token (or switch to legacy Brand Account permissions) and re-run
   `social auth youtube mantra-shakti`.
2. Until then, use the browser lane for mantra sakthi, with `--yes` for a
   one-pass upload and Save.
3. If API-only work stays unavailable, add browser flows for captions and
   playlists.
4. The repo test suites remain offline: 149 Python tests and 16 browser tests.

---

# Live run — 2026-11-04: Mantra Rahasyam Episode 1

First real episode through the tool: `/mnt/data2/Youtube/Mantrasakthi/mantra-rahasyam/ep01/publish.yaml`
→ private video `jsF4rYisfPM` on `mantra-shakti`. The upload went through the browser lane; everything
else was added on the Studio Languages and Details pages. Record: the episode folder's `publish-log.json`.

## Issues and fixes

| # | What happened | Cause | Fix / handling |
|---|---|---|---|
| 1 | `browser_unexpected_screen: locator.setInputFiles: Cannot transfer files larger than 50Mb to a browser not co-located with the server` (211 MB video) | Playwright treats a `connectOverCDP` browser as remote, so it refuses `setInputFiles` above 50 MB. | `browser/lib/files.mjs` `setLocalFiles()`: under 50 MB it still uses `setInputFiles`; above that it finds the input (shadow roots included) and calls CDP `DOM.setFileInputFiles` with the local path. Used for the video and the dub audio. Tests: `browser/test/files.test.mjs` (51 MB sparse file into a shadow-root input). |
| 2 | Retry reached `ready_to_confirm`, then `confirm` timed out in `connectOverCDP`, and the owner saw Chrome's "Reload site?" prompt | A second CDP client (a status probe) attached while the driver was between calls; the page's beforeunload prompt then blocked CDP. | Never attach a second client while a flow is mid-way. The owner clicked Cancel, then Save in the dialog (Visibility already Private). The publish log was written by hand with a warning. |
| 3 | Pressing Save while the file was still uploading | — | Fine: Studio keeps uploading as long as the Chrome window stays open. |
| 4 | Video language not set after upload; the Languages page opened a "Video language" picker first | The upload flow uses `combobox.selectOption()`, but Studio's language field is a custom dropdown, not a `<select>`. | The owner picked Telugu by hand. **The picker's "Make this the default for my channel" box is ticked by default — untick it** unless a channel-wide change is intended. The upload flow needs a real dropdown click. |
| 5 | Tags and category not set | The browser upload flow doesn't fill them. | Set on the Details page (see below). Category was already Entertainment (channel default). |
| 6 | Studio closed the per-language panel between steps | **Studio closes open panels and dialogs when the CDP client disconnects** (same family as the upload-dialog reset of 2026-11-03). | Drive multi-step Studio work from one long-lived CDP connection. One-shot `node -e` probes lose the panel each time. |
| 7 | Owner and agent clicked in the same window at once | — | Agree who drives before any Studio step; the agent stays out while the owner clicks. |
| 8 | `social yt dubs` failed: `expected screen "translations": text "Translations"` | The `add-dubs` flow was written against fixtures; live Studio differs (below). | Done by hand-driven steps this time. The flow needs a rewrite against the selectors below. |
| 9 | "Failed to add thumbnail. Please try again later." toast on Tamil | Transient. | The thumbnail was in fact attached (seen in the table after reload). Check the table before retrying. |
| 10 | An "English (United States)" row appeared with audio "Processing…" | YouTube auto-dubbing made an AI English track on its own. | Left for the owner to keep or unpublish; never removed by the tool. |

## Live Studio map (2026-11-04)

**Details page** (`/video/<id>/edit`):
- "Show more" (text match; `getByRole("button", {name: "Show more"})` was not reliable) expands advanced fields.
- Tags: `input#text-input[aria-label=Tags]`; type each tag, then Enter → chips.
- Category: `ytcp-form-select#category`.
- "Title and description language": `ytcp-form-select` with that text → dropdown → `option` "Telugu".
  Set it to the original language so localized titles work.
- Save: button "Save" (disabled again after "All changes saved").

**Languages page** (`/video/<id>/translations`):
- Header: "Video language: <lang>", buttons "Edit subtitles", "Upload manual", "Add language".
- Table columns: Language | Audio | Subtitles | Title and description | **Thumbnail**. Per-language
  thumbnails are available on this channel, which has multi-language audio. The thumbnail cell shows an
  image only, so check it by scrolling the table horizontally and taking a screenshot.
- **Add language** → `option` with the exact language name (e.g. "Hindi") → a per-language panel opens:
  - Title `div#textbox[aria-label=Title]`, Description `div#textbox[aria-label=Description]`
    (contenteditable; click then `keyboard.insertText`).
  - Rows "Thumbnail", "Audio", "Manual subtitles", each with a button "Add" (find it by the row text).
  - Thumbnail Add → file chooser directly.
  - Audio Add → "<Lang> audio" dialog → "Select file" (file chooser) → wait for "Publish" to enable →
    Publish → the row shows "Published". The track stays hidden while the video is private.
  - Manual subtitles Add → if the panel has unsaved edits, a "Save and continue?" dialog → "Save changes"
    (this saves title, description and thumbnail) → "Add subtitles" chooser: "Upload file" → radio
    "With timing" → "Continue" (file chooser) → editor → "Publish" → toast "<Lang> subtitles published".
  - Panel "Update" button saves title/description/thumbnail if not saved via the subtitles path.
- Original-language subtitles: **Upload manual** → Upload file → With timing → Continue → file. It saves
  directly with toast "<Lang> subtitles uploaded"; there is no editor and no Publish.
- An existing row (e.g. the auto-dub "English (United States)") opens the same panel when clicked by its
  name button.

## Follow-ups

1. Rewrite `add-dubs` (and add `captions`, `localize`, `thumbnails` browser commands) on the map above,
   all within one connection, with fixtures copied from the live structure.
2. Fix the upload flow's language dropdown and add tags/category to it.
3. Add `localized_thumbnails` to the manifest model (published in the episode's `publish.yaml` as an extra key).
4. Commit `browser/lib/files.mjs` + test (uncommitted at the time of writing; 18/18 browser tests pass).
