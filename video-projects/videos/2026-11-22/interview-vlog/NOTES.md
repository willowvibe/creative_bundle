# Interview vlog (2026-11-22)

Self-recorded answers to three interview questions, five vertical phone clips
(`inputs/drive-download-20261121T192042Z-1-001/`). `VID20261122003705.mp4` is
left out on purpose — kept for a separate edit.

## Deliverables (`out/`, untracked)

| File | Length | Build |
|---|---|---|
| `interview_vlog_7min_small.mp4` | 6:32.9 | **upload copy**, 240 MB: x264 slow CRF 23, AAC 128k, SSIM 0.988 vs master |
| `interview_vlog_7min.mp4` | 6:32.9 | master, 595 MB: `bash build.sh` (limit is 7:00) |
| `interview_vlog_horizontal.mp4` | 10:29 | `EDIT=full bash build.sh` |

## What the build does

- **Layout:** keep the top 1260 rows of the 1080x1920 frame (drops desk and
  lower shirt), scale to 926x1080, centre on a blurred, darkened copy.
- **Clip edges:** each clip starts with the hand leaving the record button and
  ends with the hand reaching to stop. Cut ~0.25 s before the first word and
  before the reach is visible in the cropped frame (checked frame by frame).
- **Audio:** per-clip level match → highpass 80 → `afftdn` → EQ (−2 dB 250 Hz,
  +2 dB 3.5 kHz) → compressor → `dynaudnorm` (t= so silence is not lifted) →
  gate (−30 dB range) → per-segment trim → gain + limiter. Result −15.7 LUFS,
  segments within ~0.5 dB, pauses about −69 dB (as quiet as the raw recording).
- **Sync:** every cut snapped to a 1/30 s frame; each segment encoded with an
  exact frame count (`tpad` + `-frames:v`), then stream-copy concat.

## 7-minute cut

Transcript via Sarvam `saarika:v2.5` (en-IN), chunked at pauses, boundary
phrases re-split at 0.06–0.12 s pauses. Removed: repeated Polars-vs-Pandas
(3×), SQL-separates-business-logic (3×), LLM-team data (2×), the "sorry, I'll
explain the recent project again" restart, all of clip 4 (primary architect /
modular repo — repeats tech-lead and modular points), Continental tools,
Oracle/SFTP details, Airflow DevOps hosting, a repeated "8 years of Python".

## Open

- Years of experience still disagree on screen: "2.5 years data engineering"
  (0:28), "pipelines 3–3.5 years" (1:43), "Airflow nearly 4 years" (5:51).
  The "last 5 years plus" line was cut. Re-record or confirm before submitting.
