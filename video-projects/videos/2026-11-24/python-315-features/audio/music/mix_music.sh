#!/usr/bin/env bash
# Music bed + SFX mix for EP1 v5. usage: bash mix_music.sh   (run from anywhere)
# Bed: PoYo Suno takes bed_1 + bed_2, 4 s crossfade, under the main part only (4.0 s .. 309.2 s; intro/outro keep
# their own sound). Level: -9 dB, ducked about 15-16 dB under the voice (~ -31 LUFS while speaking), ducked under the narration by a sidechain compressor.
# SFX: gain chosen so their loudness is +5 dB over the ducked bed (user's rule), computed below from measurements.
set -euo pipefail
D="$(cd "$(dirname "$0")/../.." && pwd)"
O="$D/out"; M="$D/audio/music"
BED_DB=-11.0   # user: 2 dB quieter than the first v5 (-9.0)
MAIN_START=4.0; MAIN_LEN=305.2
ffmpeg -v error -y -i "$M/bed_1.mp3" -i "$M/bed_2.mp3" -filter_complex \
  "[0:a][1:a]acrossfade=d=4:c1=tri:c2=tri,aresample=44100,aformat=channel_layouts=stereo,atrim=0:$MAIN_LEN,afade=t=in:d=1.5,afade=t=out:st=$(python3 -c "print($MAIN_LEN-3)"):d=3,volume=${BED_DB}dB,adelay=$(python3 -c "print(int($MAIN_START*1000))")|$(python3 -c "print(int($MAIN_START*1000))"),apad=whole_dur=321.2[bed]" \
  -map "[bed]" -c:a pcm_s16le "$M/_bed.wav"
# duck under the voice (sidechain = the v3 programme audio, which is narration in the main part)
ffmpeg -v error -y -i "$M/_bed.wav" -i "$O/python315_v3.mp4" -filter_complex \
  "[1:a]aresample=44100,aformat=channel_layouts=stereo[sc];[0:a][sc]sidechaincompress=threshold=0.02:ratio=3:attack=40:release=500:makeup=1[d]" \
  -map "[d]" -c:a pcm_s16le "$M/_bed_ducked.wav"
bed_i=$(ffmpeg -hide_banner -i "$M/_bed_ducked.wav" -af ebur128 -f null - 2>&1 | awk '/^ +I:/{v=$2} END{print v}')
sfx_i=$(ffmpeg -hide_banner -i "$O/sfx/sfx.wav" -af ebur128 -f null - 2>&1 | awk '/^ +I:/{v=$2} END{print v}')
SFX_DB=5.3   # fixed: the level the user approved in v5 (was bed+5 when the bed sat at -9 dB)
sfx_db=$SFX_DB
echo "ducked bed $bed_i LUFS, sfx $sfx_i LUFS, sfx gain ${sfx_db} dB (fixed)"
ffmpeg -v error -y -i "$O/python315_v3.mp4" -i "$M/_bed_ducked.wav" -i "$O/sfx/sfx.wav" -filter_complex \
  "[2:a]volume=${sfx_db}dB[s];[0:a][1:a][s]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.89:level=0[a]" \
  -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -movflags +faststart "$O/python315_v5.mp4"
rm -f "$M/_bed.wav"
echo "wrote $O/python315_v5.mp4"
