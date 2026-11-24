#!/usr/bin/env bash
# Interview vlog: 5 vertical phone clips -> one 1920x1080 horizontal video.
# Left out on purpose: VID20261122003705.mp4 (kept for a separate edit).
#
# Video: crop the lower part of the frame (desk + lower shirt), keep the
#        top CROP_H rows, centre it on a blurred, darkened copy of itself.
# Audio: per-clip level match -> highpass -> FFT denoise -> light EQ ->
#        compressor -> leveller -> gate -> per-segment trim -> -14 LUFS gain
#        + limiter.
#
# EDIT=full  : whole clips, heads/tails trimmed (10:29)
# EDIT=7min  : repeats and side-tracks cut (default, ~6:33, limit is 7:00)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
IN="$HERE/../../../inputs/drive-download-20261121T192042Z-1-001"
OUT="$HERE/out"; mkdir -p "$OUT"
CLIPS=(VID20261122002233 VID20261122002252 VID20261122002513 VID20261122003036 VID20261122003116)
EDIT=${EDIT:-7min}

CROP_H=${CROP_H:-1260}          # rows kept from the top of the 1080x1920 frame
FG_W=$(( (1080*1080/CROP_H) / 2 * 2 ))

# --- 1. segments: "clip_index in out" in SOURCE seconds.
# Clip heads/tails: each clip opens with the hand leaving the record button
# plus 1-2 s of silence and closes with the hand reaching to stop; keep
# ~0.25 s before the first word and stop before the reach is in view
# (silencedetect + frame checks). Clip 2's 121.3-121.9 s is earphone rustle.
if [[ $EDIT == full ]]; then
  SEGS=("0 1.42 11.97" "1 1.20 120.48" "2 1.00 307.67" "3 1.66 33.46" "4 0.87 162.03")
  NAME=interview_vlog_horizontal
else
  # Cuts chosen from a Sarvam STT transcript (phrase-level, re-split at
  # pauses at each boundary); every cut sits in a pause. Removed:
  #  c2 72.1-93.5   Continental: Pandas->Polars->Iceberg (repeated later, mumbled)
  #  c2 119.7-      stray "So"
  #  c3 67.9-82.5   Oracle/SFTP server details
  #  c3 137.4-148.7 "sorry ... I'll explain the recent project again" restart
  #  c3 182.3-198.2 tool knowledge kept by one or two people (long reasons list)
  #  c3 229.2-286.4 Pandas efficiency, SQL logic (3rd time), Airflow DevOps hosting
  #  c4 (all)       primary architect / modular repo (repeats tech-lead + modular)
  #  c5 13.4-28.0   "8 years ... last 5 years plus data eng" (repeat, contradicts 2.5 y)
  #  c5 82.5-151.4  Pandas vs Polars again + LLM-team data again
  SEGS=("0 1.433333 11.966667"
        "1 1.200000 72.136666" "1 93.456666 119.666666"
        "2 1.230000 67.920000" "2 82.470000 137.380000" "2 148.650000 182.290000"
        "2 198.160000 229.200000" "2 286.410000 307.666666"
        "4 0.866667 13.370000" "4 28.010000 82.500000" "4 151.370000 162.033333")
  NAME=interview_vlog_7min
fi

# Snap to 30 fps frame boundaries so every audio piece is exactly as long as
# its video piece and lip-sync does not drift across joins.
# printf adds the leading zero bc drops (".866" is rejected by ffmpeg -ss).
snap(){ printf '%.6f' "$(echo "scale=6; x=$1*30+0.5; scale=0; x=x/1; scale=6; x/30" | bc -l)"; }
S_CLIP=(); S_IN=(); S_OUT=()
for s in "${SEGS[@]}"; do
  read -r c a b <<<"$s"
  S_CLIP+=("$c"); S_IN+=("$(snap "$a")"); S_OUT+=("$(snap "$b")")
done
NSEG=${#SEGS[@]}

lufs(){ ffmpeg -hide_banner "$@" -af ebur128=framelog=quiet -f null - 2>&1 | awk '/  I:/{print $2}' | tail -1; }

# --- 2. audio
# Per-clip gain to a common level first (clip 1 was recorded ~2 dB quieter),
# measured over each clip's used range (head/tail trimmed).
declare -A CLIP_GAIN
for c in $(printf '%s\n' "${S_CLIP[@]}" | sort -u); do
  lo=999999; hi=0
  for k in $(seq 0 $((NSEG-1))); do
    [[ ${S_CLIP[$k]} == "$c" ]] || continue
    (( $(echo "${S_IN[$k]} < $lo" | bc -l) )) && lo=${S_IN[$k]}
    (( $(echo "${S_OUT[$k]} > $hi" | bc -l) )) && hi=${S_OUT[$k]}
  done
  ci=$(lufs -ss "$lo" -to "$hi" -i "$IN/${CLIPS[$c]}.mp4" -vn)
  CLIP_GAIN[$c]=$(echo "-16.5 - ($ci)" | bc -l)
done

: > "$OUT/alist.txt"
for k in $(seq 0 $((NSEG-1))); do
  c=${S_CLIP[$k]}; d=$(echo "${S_OUT[$k]} - ${S_IN[$k]}" | bc -l)
  ffmpeg -v error -y -ss "${S_IN[$k]}" -to "${S_OUT[$k]}" -i "$IN/${CLIPS[$c]}.mp4" -vn -ac 1 -ar 48000 \
    -af "volume=${CLIP_GAIN[$c]}dB,afade=t=in:d=0.02,afade=t=out:st=$(echo "$d - 0.02" | bc -l):d=0.02" \
    "$OUT/a_$k.wav"
  echo "file '$OUT/a_$k.wav'" >> "$OUT/alist.txt"
done
ffmpeg -v error -y -f concat -safe 0 -i "$OUT/alist.txt" -c copy "$OUT/raw.wav"
rm -f "$OUT"/a_*.wav

# dynaudnorm t= stops it lifting near-silence; the gate then pulls the
# pauses back down (-30 dB range) so leveling does not raise room hiss.
# Measured on v1 (no gate): pause noise rose -69 -> -55 dB. With gate: -71 dB.
CHAIN="highpass=f=80,afftdn=nr=18:nf=-45:tn=1,\
equalizer=f=250:t=q:w=1.2:g=-2,equalizer=f=3500:t=q:w=1.0:g=2,\
acompressor=threshold=-24dB:ratio=3:attack=10:release=200:makeup=2,\
dynaudnorm=f=300:g=15:m=5:p=0.9:r=0.5:t=0.003,\
agate=threshold=0.025:range=0.03:ratio=4:attack=3:release=250:knee=3"
ffmpeg -v error -y -i "$OUT/raw.wav" -af "$CHAIN" "$OUT/clean_pre.wav"

# Second pass: re-measure each segment (>=8 s) after processing and trim any
# that sit >0.5 dB off the whole-film level.
ALL=$(lufs -i "$OUT/clean_pre.wav"); T=0; VF=""
for k in $(seq 0 $((NSEG-1))); do
  d=$(echo "${S_OUT[$k]} - ${S_IN[$k]}" | bc -l)
  if (( $(echo "$d >= 8" | bc -l) )); then
    ci=$(lufs -ss "$T" -t "$d" -i "$OUT/clean_pre.wav")
    delta=$(echo "$ALL - ($ci)" | bc -l)
    if (( $(echo "${delta#-} > 0.5" | bc -l) )); then
      VF+="${VF:+,}volume=${delta}dB:enable='between(t,$T,$(echo "$T+$d" | bc -l))'"
    fi
  fi
  T=$(echo "$T + $d" | bc -l)
done
if [[ -n "$VF" ]]; then
  ffmpeg -v error -y -i "$OUT/clean_pre.wav" -af "$VF" "$OUT/clean_pre2.wav" && mv "$OUT/clean_pre2.wav" "$OUT/clean_pre.wav"
fi
# Static gain to -14 LUFS, then a peak-safe limiter.
I=$(lufs -i "$OUT/clean_pre.wav")
GAIN=$(echo "-14 - ($I)" | bc -l)
ffmpeg -v error -y -i "$OUT/clean_pre.wav" -af \
  "volume=${GAIN}dB,alimiter=limit=0.7:attack=2:release=50:level=false,aresample=48000" \
  -c:a pcm_s16le "$OUT/clean_$EDIT.wav"
rm -f "$OUT/raw.wav" "$OUT/clean_pre.wav"

# --- 3. video: one encode per segment (keeps memory low: one decoder at a
# time), then a stream-copy concat. Autorotate fixes the -180 matrix.
VF_LAYOUT="fps=30,crop=1080:$CROP_H:0:0,setsar=1,split[a][b];\
[a]scale=1920:-2,crop=1920:1080,boxblur=30:2,eq=brightness=-0.08:saturation=0.8[bg];\
[b]scale=$FG_W:1080[fg];[bg][fg]overlay=(W-w)/2:0,format=yuv420p"
: > "$OUT/vlist.txt"
for k in $(seq 0 $((NSEG-1))); do
  echo "segment $((k+1))/$NSEG"
  # Exact frame count per segment: the input seek can drop the boundary
  # frame (v1 of the 7-min edit came out 2 frames short = 66 ms drift), so
  # pad by cloning the last frame and cap with -frames:v.
  NF=$(printf '%.0f' "$(echo "(${S_OUT[$k]} - ${S_IN[$k]}) * 30" | bc -l)")
  ffmpeg -v error -y -ss "${S_IN[$k]}" -to "${S_OUT[$k]}" -i "$IN/${CLIPS[${S_CLIP[$k]}]}.mp4" -an \
    -filter_complex "[0:v]tpad=stop_mode=clone:stop_duration=0.2,$VF_LAYOUT" -frames:v "$NF" \
    -c:v libx264 -preset medium -crf 18 -r 30 \
    -g 60 -bf 2 "$OUT/v_$k.mp4"
  echo "file '$OUT/v_$k.mp4'" >> "$OUT/vlist.txt"
done
ffmpeg -v error -y -f concat -safe 0 -i "$OUT/vlist.txt" -i "$OUT/clean_$EDIT.wav" \
  -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -movflags +faststart -shortest "$OUT/$NAME.mp4"
rm -f "$OUT"/v_*.mp4 "$OUT/vlist.txt" "$OUT/alist.txt"

echo "done: $OUT/$NAME.mp4"
