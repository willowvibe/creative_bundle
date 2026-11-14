# Drama review board

A Cloudflare Worker where remote reviewers check each stage of a drama episode, one item at a time:
script → dialogue → audio → images → videos → lip-sync + SFX → final cut. Live at
https://drama-review.ngharish-conti.workers.dev (free tier, no domain).

- **Reviewers** open a personal link (`?k=<token>`), no account needed. On each line, take, frame or clip they tap
  *Looks good* or *Needs change* (with a comment), or add a note. Comments are tied to that item's version: a
  regenerated item shows old comments as "on an earlier version".
- **The owner** also sees an *Approve the whole stage* button. `drama_board.py pull` records that approval in the
  local gate (`scripts/drama_review.py`) only if the files are unchanged since publishing.
- **Storage:** R2 bucket `drama-review` (manifests + media), D1 database `drama-review` (reviewers, feedback, sign-offs).

```bash
python scripts/drama_board.py publish dialogue videos/<date>/<episode> "note for reviewers"
python scripts/drama_board.py invite "Name"      # prints that person's link to send on WhatsApp
python scripts/drama_board.py pull dialogue videos/<date>/<episode>   # -> review/feedback_dialogue.md
```

Setup (already done): `wrangler r2 bucket create drama-review`, `wrangler d1 create drama-review`,
`wrangler d1 execute drama-review --remote --file schema.sql`, `wrangler deploy`, then insert one `owner` row into
`reviewers` and put `DRAMA_REVIEW_URL` / `DRAMA_REVIEW_KEY` in `video-editor/.env`.

A personal link works like a password: anyone holding it can comment as that person. To revoke one, delete its
row from `reviewers`.
