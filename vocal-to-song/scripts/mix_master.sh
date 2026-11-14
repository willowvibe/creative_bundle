#!/usr/bin/env bash
# Mix tuned vocal + tanpura + pad with hall reverb, master to -14 LUFS (approved v4 levels).
# usage: mix_master.sh VOCAL_TUNED TANPURA PAD IR OUT_BASENAME TOTAL_S [INTRO_S=3] [REVERB=0.13] [TAN_DB=-17] [PAD_DB=-29]
set -euo pipefail
VOC=$1 TAN=$2 PAD=$3 IR=$4 OUT=$5 TOTAL=$6
INTRO=${7:-3} REV=${8:-0.13} TAN_DB=${9:--17} PAD_DB=${10:--29}
MS=$(python3 -c "print(int(float('$INTRO')*1000))")
PRE="${OUT}_premaster.wav"

ffmpeg -hide_banner -loglevel error -y -i "$VOC" -i "$TAN" -i "$PAD" -i "$IR" -filter_complex "
[0:a]aresample=44100,highpass=f=90,equalizer=f=250:t=q:w=1:g=-2,equalizer=f=3500:t=q:w=1.2:g=2.5,deesser=i=0.3,acompressor=threshold=-20dB:ratio=3:attack=10:release=150:makeup=3,pan=stereo|c0=c0|c1=c0,adelay=${MS}|${MS},apad=whole_dur=${TOTAL},asplit[vd][vw];
[vw][3:a]afir=dry=10:wet=10[rev];
[vd][rev]amix=inputs=2:weights='1 ${REV}':normalize=0[voc];
[1:a]volume=${TAN_DB}dB,highpass=f=60[tan];
[2:a]volume=${PAD_DB}dB,lowpass=f=1200[pad];
[voc][tan][pad]amix=inputs=3:normalize=0,atrim=0:${TOTAL}[m]" -map "[m]" -c:a pcm_s24le "$PRE"

# two-pass loudnorm: measure, then apply linearly
J=$(ffmpeg -hide_banner -i "$PRE" -af loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json -f null - 2>&1 | grep -A12 '^{')
g() { echo "$J" | grep "\"$1\"" | sed 's/.*: "\(.*\)".*/\1/'; }
ffmpeg -hide_banner -loglevel error -y -i "$PRE" -af "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=$(g input_i):measured_TP=$(g input_tp):measured_LRA=$(g input_lra):measured_thresh=$(g input_thresh):offset=$(g target_offset):linear=true" -ar 44100 -c:a pcm_s24le "${OUT}.wav"
ffmpeg -hide_banner -loglevel error -y -i "${OUT}.wav" -c:a libmp3lame -b:a 320k "${OUT}.mp3"
rm -f "$PRE"
ffmpeg -hide_banner -i "${OUT}.wav" -af ebur128=peak=true -f null - 2>&1 | grep -E "I:|Peak:" | tail -2
