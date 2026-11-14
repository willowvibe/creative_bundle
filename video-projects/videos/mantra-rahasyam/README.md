# మంత్ర రహస్యం (Mantra Rahasyam): series

Telugu drama series for the **Mantra Shakti** YouTube channel (@Mantrashakti-power). 16:9, Telugu audio, separate
English `.srt`.

## Episodes
| # | Title | Folder | Status |
|---|---|---|---|
| 1 | మృత్యువుతో పోరాటం (The Battle with Death) | `episodes/ep01` → `videos/2026-10-30/mantra-rahasyam-ep1/` | **v2 finished 2026-11-02**: `mantra_rahasyam_ep1_v2_final.mp4` (5:16.88), thumbnail + 3 Shorts + chapters + te/en/hi/ta `.srt`, Hindi/Tamil audio tracks (see its README) |
| 2 | శ్రీశైలం యాత్ర (The Srisailam Pilgrimage) | `episodes/ep02` → `videos/2026-11-04/mantra-rahasyam-ep2/` | script gate: `script.md` drafted 2026-11-04 (8–10 min; Chitragupta's ledger, 108-lives counter, manuscript reveal) |

Each episode lives in its own dated folder (`videos/<date>/mantra-rahasyam-epN/`); `episodes/epNN` links to it.

## Shared assets (`assets/`): reuse these in every episode
| Path | What |
|---|---|
| `assets/characters/abhinav/` | hero: bible, approved reference images |
| `assets/characters/lakshmi/` | mother: bible, references |
| `assets/characters/raghava/` | father: bible, references |
| `assets/characters/shiva/` | Lord Shiva: bible, two frames of the user's Flow clip as references |
| `assets/audio/mantra_cycle.wav` | the channel's Mahamrityunjaya chant, one 11.5 s cycle (`j4dxjdQKaD4`, 25.7–37.2 s) |

Each character's `bible.md` is the source of truth for look, wardrobe, voice (ElevenLabs id and settings),
personality, story so far and continuity rules. Its **image prompt block** goes word for word into every image
prompt for that character. After each episode, update "Story so far" and anything that changed (scars, new items).

Characters that appear in only one episode (doctor, nurses, passerby, Yama Dhoothas) stay in that episode's
`shots.json`. They move here if they come back.

## Production rules
Pattern and measured costs: `docs/DRAMA_PRODUCTION_PLAYBOOK.md` (skill: `drama-episode`). Copy the Ep1
`edit/v2_*.py` scripts for a new episode.
- Gates per episode: script → dialogue → audio → images → (animatic) → videos → final cut, then promo, then
  other-language audio (`scripts/drama_review.py`; remote reviewers on the board, `scripts/drama_board.py`).
- **Other-language dub audio only after the final cut is approved** (user rule, 2026-11-02).
- **Look:** bright, natural daylight, never dark or grim. Stills mostly on **Flow Nano Banana Pro** (free, best
  expressions); PoYo **Kling O3** for calm shots, **Qwen Image 3** for blood. Remove Flow's sparkle mark.
- **Shot grammar:** stills first, lip-sync only where a character is seen speaking (one window per shot);
  faces always move (living stills), camera-only moves for places and inserts.
- **Video (Ep1 v2 route):** PoYo **OmniHuman 2.0** lip-sync, **Veo 3.1 Lite** motion, **Kling O3** where Veo refuses,
  **Flow Omni 1.1 Flash** for divine/effects. Scenes the user makes in Google Flow are used whole (remove the logo).
- **Voices:** ElevenLabs `eleven_v4`, same voice per character across episodes (and across dub languages), never two
  characters on one voice; narrator at ~30% emotion. 0.5 s gaps between lines.
- **Finish:** HyperFrames title, supers, name tags, on-screen mantra, 4 s cold open, 15 s end card with the channel
  icon and name (no link).
