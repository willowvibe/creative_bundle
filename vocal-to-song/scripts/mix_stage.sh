#!/usr/bin/env bash
# usage: mix_stage.sh STEMDIR OUT_BASENAME
set -euo pipefail
D=$1 OUT=$2
TOTAL=$(cat "$D/total.txt")
DBL=${DBL:--9} TAN=${TAN:--17} PAD=${PAD:--29} MAN=${MAN:--27} MRI=${MRI:--22} FLU=${FLU:--12} BEL=${BEL:--20}
VCH="highpass=f=90,equalizer=f=250:t=q:w=1:g=-2,equalizer=f=3500:t=q:w=1.2:g=2.5,deesser=i=0.3,acompressor=threshold=-20dB:ratio=3:attack=10:release=150:makeup=3"
PRE="${OUT}_pre.wav"
ffmpeg -hide_banner -loglevel error -y \
  -i "$D/voc_main.wav" -i "$D/voc_dbl.wav" -i "$D/tanpura.wav" -i "$D/pad.wav" \
  -i "$D/manjira.wav" -i "$D/mridangam.wav" -i "$D/flute.wav" -i "$D/bell.wav" -i hall_ir.wav -filter_complex "
[0:a]$VCH,pan=stereo|c0=c0|c1=c0[vm];
[1:a]$VCH,volume=${DBL}dB[vd];
[vm][vd]amix=inputs=2:normalize=0,asplit[vbus][vsend];
[8:a]asplit=3[ir1][ir2][ir3];
[vsend][ir1]afir=dry=10:wet=10,volume=0.13[vrev];
[6:a]volume=${FLU}dB,highpass=f=200,asplit[fl][fsend];
[fsend][ir2]afir=dry=10:wet=10,volume=0.30[frev];
[4:a]volume=${MAN}dB[man];
[5:a]volume=${MRI}dB,highpass=f=50[mri];
[man][mri]amix=inputs=2:normalize=0,asplit[perc][psend];
[psend][ir3]afir=dry=10:wet=10,volume=0.08[prev];
[2:a]volume=${TAN}dB,highpass=f=60[tan];
[3:a]volume=${PAD}dB,lowpass=f=1200[pad];
[7:a]volume=${BEL}dB[bel];
[vbus][vrev][fl][frev][perc][prev][tan][pad][bel]amix=inputs=9:normalize=0,atrim=0:${TOTAL}[m]" \
  -map "[m]" -c:a pcm_s24le "$PRE"
J=$(ffmpeg -hide_banner -i "$PRE" -af loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json -f null - 2>&1 | grep -A12 '^{')
g() { echo "$J" | grep "\"$1\"" | sed 's/.*: "\(.*\)".*/\1/'; }
ffmpeg -hide_banner -loglevel error -y -i "$PRE" -af "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=$(g input_i):measured_TP=$(g input_tp):measured_LRA=$(g input_lra):measured_thresh=$(g input_thresh):offset=$(g target_offset):linear=true" -ar 44100 -c:a pcm_s24le "${OUT}.wav"
ffmpeg -hide_banner -loglevel error -y -i "${OUT}.wav" -c:a libmp3lame -b:a 320k "${OUT}.mp3"
rm -f "$PRE"
ffmpeg -hide_banner -i "${OUT}.wav" -af ebur128=peak=true -f null - 2>&1 | grep -E "I:|Peak:" | tail -2
