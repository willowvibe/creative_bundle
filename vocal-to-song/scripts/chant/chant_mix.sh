#!/usr/bin/env bash
# usage: chant_mix.sh STEMDIR OUTBASE [REV=0.13] [TAN_DB=-17] [PAD_DB=-29] [BELL_DB=-20]
set -euo pipefail
D=$1 OUT=$2 REV=${3:-0.13} TAN=${4:--17} PAD=${5:--29} BELL=${6:--20}
PRE="${OUT}_pre.wav"
ffmpeg -hide_banner -loglevel error -y -i $D/voice_108.wav -i $D/tanpura.wav -i $D/pad.wav -i $D/bell.wav -i $D/hall_ir.wav -filter_complex "
[0:a]volume=6dB,pan=stereo|c0=c0|c1=c0,asplit[vd][vw];
[vw][4:a]afir=dry=10:wet=10[rev];
[vd][rev]amix=inputs=2:weights='1 ${REV}':normalize=0[voc];
[1:a]volume=${TAN}dB,highpass=f=55[tan];
[2:a]volume=${PAD}dB,lowpass=f=1000[pad];
[3:a]volume=${BELL}dB[bel];
[tan][pad][bel]amix=inputs=3:normalize=0,asplit[bk][bkout];
[voc][bk]amix=inputs=2:normalize=0,acompressor=threshold=-18dB:ratio=1.8:attack=30:release=300:makeup=1[m]" \
 -map "[m]" -c:a pcm_s24le "$PRE" -map "[bkout]" -c:a pcm_f32le "${OUT}_backing_only.wav"
J=$(ffmpeg -hide_banner -i "$PRE" -af loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json -f null - 2>&1 | grep -A12 '^{')
g() { echo "$J" | grep "\"$1\"" | sed 's/.*: "\(.*\)".*/\1/'; }
ffmpeg -hide_banner -loglevel error -y -i "$PRE" -af "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=$(g input_i):measured_TP=$(g input_tp):measured_LRA=$(g input_lra):measured_thresh=$(g input_thresh):offset=$(g target_offset):linear=true" -ar 44100 -c:a pcm_s24le "${OUT}.wav"
ffmpeg -hide_banner -loglevel error -y -i "${OUT}.wav" -c:a libmp3lame -b:a 320k "${OUT}.mp3"
rm -f "$PRE"
