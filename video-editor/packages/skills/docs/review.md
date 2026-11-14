# Post-render review skill

Deterministic quality gate that runs on the final exported MP4.

- **Critical** checks hard-fail the build when they fail (e.g. missing audio,
  silent output, unexpected black frames, invalid duration).
- **Quality** checks produce a report card without blocking the build
  (e.g. aspect ratio drift, loudness, FPS).

The skill writes `work_dir/review/review_report.json` and logs a `review`
stage to the job manifest.
