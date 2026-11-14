# మంత్ర రహస్యం: Episode 2, శ్రీశైలం యాత్ర

Telugu drama for the Mantra Shakti channel, 16:9, English `.srt`, target ≈ 9:35. Template episode:
`../../2026-10-30/mantra-rahasyam-ep1/` (skill: `drama-episode`). Series root: `../../mantra-rahasyam/`.

## Stage log

| Date | Stage | State | Spend |
|---|---|---|---|
| 2026-11-04 | 1 Script, v1 | Drafted and published to the board (`review/feedback_script.md`: not reviewed yet) | — |
| 2026-11-04 | 1 Script, v2 | Viewer-hook pass in `script.md` (v1 kept as `script.v1.md`). Published to the board 2026-11-04. Adds a question map, scene endings, a shot plan with 4 scene3d directed shots, look, sound cue sheet, pacing, packaging. **No dialogue changed.** Story changes marked **[v2]**, listed under "Open for review" 5–10. Gate not approved yet. | — |
| 2026-11-04 | 1 Script, v2.1 | Shot plan: the 5 directed camera shots move to **Uni3C on the approved stills** (≈ $0.18 each, scene3d as fallback; spike verdict in `scene3d-engine/docs/plans/2026-11-04-uni3c-spike.md`). Open item 10 updated. Story and dialogue unchanged. Republished to the board. | — |

## Next

- The family reviews v2 on the board (open items 1–10). Read their notes with
  `scripts/drama_board.py pull script <episode>`; the gate is approved only by the owner's press on the board.
- `shots.json` (te/en/emotion per line) is still to be built from the dialogue draft. Then run
  `scripts/dialogue_check.py` before the dialogue gate.
- Before the animatic gate: read Ep1's YouTube retention graph (public Fri 6 Nov 2026) and tighten the dip zones.
- Directed shots (Uni3C) run after the images gate, from the approved stills: `s3d still-move` then `rpc run wan21_uni3c`. Faces wait for a character-still test.

## Budget estimate (2026-11-05)

≈ **$15–25 + 60–100 Flow credits** with Flow, ≈ **$25–40** without; plus ~$5 for dubs in 4 languages. The
breakdown is in `docs/DRAMA_PRODUCTION_PLAYBOOK.md` §11. Ep1 cost $17.57 + 55 Flow credits for 5:17; Ep2 is about
1.8× longer but uses H3 lip-sync (about 4× cheaper than OmniHuman).
