#!/usr/bin/env bash
# EP2 final cut: intro + avatar render + outro, then music bed + SFX.  usage: bash mix.sh   (run from anywhere)
# Bed: EP1's PoYo Suno takes (reused, no new spend), 4 s crossfade, under the main part only; level -11 dB and ducked
# under the voice by a sidechain compressor (the user's EP1 levels). SFX gain fixed at +5.3 dB (approved in EP1).
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
O="$D/out"; M="$D/../../2026-11-24/python-315-features/audio/music"
BED_DB=-11.0; SFX_DB=5.3; INTRO=4.0
MAIN_LEN=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$O/uv_v1.mp4")
# 1. join (concat filter re-encodes, so small timestamp differences between the parts don't matter)
ffmpeg -v error -y -i "$O/intro.mp4" -i "$O/uv_v1.mp4" -i "$O/outro.mp4" -filter_complex \
  "[0:v]fps=30,format=yuv420p[v0];[1:v]fps=30,format=yuv420p[v1];[2:v]fps=30,format=yuv420p[v2];\
[0:a]aresample=44100,aformat=channel_layouts=stereo[a0];[1:a]aresample=44100,aformat=channel_layouts=stereo[a1];\
[2:a]aresample=44100,aformat=channel_layouts=stereo[a2];[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]" \
  -map "[v]" -map "[a]" -c:v libx264 -preset medium -crf 18 -c:a aac -b:a 192k "$O/uv_v2.mp4"
TOTAL=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$O/uv_v2.mp4")
# 2. bed under the main part
ffmpeg -v error -y -i "$M/bed_1.mp3" -i "$M/bed_2.mp3" -filter_complex \
  "[0:a][1:a]acrossfade=d=4:c1=tri:c2=tri,aresample=44100,aformat=channel_layouts=stereo,atrim=0:$MAIN_LEN,afade=t=in:d=1.5,afade=t=out:st=$(python3 -c "print($MAIN_LEN-3)"):d=3,volume=${BED_DB}dB,adelay=$(python3 -c "print(int($INTRO*1000))")|$(python3 -c "print(int($INTRO*1000))"),apad=whole_dur=$TOTAL[bed]" \
  -map "[bed]" -c:a pcm_s16le "$O/_bed.wav"
ffmpeg -v error -y -i "$O/_bed.wav" -i "$O/uv_v2.mp4" -filter_complex \
  "[1:a]aresample=44100,aformat=channel_layouts=stereo[sc];[0:a][sc]sidechaincompress=threshold=0.02:ratio=3:attack=40:release=500:makeup=1[d]" \
  -map "[d]" -c:a pcm_s16le "$O/_bed_ducked.wav"
# 3. final mix (limiter level=0: no make-up gain)
ffmpeg -v error -y -i "$O/uv_v2.mp4" -i "$O/_bed_ducked.wav" -i "$O/sfx/sfx.wav" -filter_complex \
  "[2:a]volume=${SFX_DB}dB[s];[0:a][1:a][s]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.89:level=0[a]" \
  -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -movflags +faststart "$O/uv_v3.mp4"
rm -f "$O/_bed.wav"
ffmpeg -hide_banner -i "$O/uv_v3.mp4" -af ebur128=peak=true -f null - 2>&1 | grep -E '^\s+(I|Peak):' | tail -2
echo "wrote $O/uv_v3.mp4 ($TOTAL s)"
