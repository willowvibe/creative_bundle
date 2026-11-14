#!/usr/bin/env bash
# Render the Om Saravana Bhava x108 video, 1920x1080 at 30 fps, synced to layout_final.json.
#   chants 1-98:   drifting temple-glow background + Tamil Om sign, a glow on every ding, and a light
#                  wave of falling petals just after each ding (the 8.3 s loop is locked to the chant grid)
#   chants 99-108: five Muruga images, two chants each, each with its own slow zoom or tilt, under a
#                  pushpanjali petal shower; the transitions are fadewhite, circleopen, hblur,
#                  fade and circleopen
#   always:        golden glitter, the Tamil + English chant caption centred at the 75% line, and
#                  the "n / 108" japa counter
# usage: bash render.sh PROJECT_DIR OUT.mp4   (needs art/ from make_art.py and fx.py)
set -euo pipefail
P=$1 OUT=$2
A=$P/art I=$P/images
# timing from layout_final.json: chant k (1-based) starts at 5 + 8.3(k-1); its ding at 12.25 + 8.3(k-1)
T1=817.35   # ding after chant 98: the images begin
SEG=16.6    # two chant periods per image
TOTAL=908.3
py() { python3 -c "print(round($1, 3))"; }
IMG=(m1_vel_peacock__agy.jpg m2_bala_murugan__agy.jpg m3_portrait__agy.jpg m4_dandayuthapani__agy.jpg m5_temple_arati__agy.jpg)
# per image: transition INTO it, its length, and its move (z0 z1 fx0 fy0 fx1 fy1); fx/fy = focus as a fraction of the frame
XFT=(fadewhite circleopen hblur fade circleopen)  # radial cut a hard edge across a face and dissolve speckled one (v2 QA)
XFD=(3 2 2 2 2)
MOVE=("1.00 1.22 0.51 0.36 0.51 0.30"   # m1: slow push in toward the face
      "1.25 1.02 0.60 0.32 0.55 0.45"   # m2: Bala Murugan, zoom out to reveal the pond
      "1.00 1.18 0.50 0.48 0.50 0.40"   # m3: portrait, gentle push in
      "1.30 1.22 0.50 0.78 0.50 0.24"   # m4: Dandayuthapani, tilt from his feet up to his face
      "1.32 1.00 0.50 0.30 0.50 0.50")  # m5: sanctum, pull back to the whole shrine
FONT=/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf
mkdir -p "$A/hi"
for f in "${IMG[@]}"; do  # pre-upscale once so zoompan moves smoothly instead of stepping by whole pixels
  [ -f "$A/hi/${f%.*}.png" ] || ffmpeg -hide_banner -loglevel error -y -i "$I/$f" -vf scale=3840:2160:flags=lanczos "$A/hi/${f%.*}.png"
done

# clip k length = SEG + the next transition's length (the last one runs to the end)
DUR=()
for k in 0 1 2 3; do DUR+=("$(py "$SEG + ${XFD[$((k+1))]}")"); done
DUR+=("$(py "$TOTAL - ($T1 + 4*$SEG)")")

SCRIPT=$(mktemp --suffix=.ffgraph)
{
  echo "[0:v]crop=1920:1080:x='192+150*sin(2*PI*t/97)':y='108+90*sin(2*PI*t/131)',format=rgba[bgc];"
  echo "[bgc][1:v]overlay=0:0:format=auto,format=yuv420p,setsar=1[segA];"
  for k in 0 1 2 3 4; do
    read -r z0 z1 fx0 fy0 fx1 fy1 <<< "${MOVE[$k]}"
    N=$(python3 -c "print(int(round(${DUR[$k]}*30)))")
    # eased progress e(on) = smoothstep; zoom and focus interpolate along it; x/y clamped inside the image
    E="(3*pow(on/$N,2)-2*pow(on/$N,3))"
    Z="($z0+($z1-$z0)*$E)"
    FX="($fx0+($fx1-$fx0)*$E)"
    FY="($fy0+($fy1-$fy0)*$E)"
    echo "[$((k+6)):v]zoompan=z='$Z':x='max(0,min(iw-iw/zoom,iw*$FX-iw/zoom/2))':y='max(0,min(ih-ih/zoom,ih*$FY-ih/zoom/2))':d=1:s=1920x1080:fps=30,format=rgba[im${k}r];"
    echo "[im${k}r][3:v]overlay=0:0:format=auto,format=yuv420p,setsar=1[im${k}];"
  done
  off=$T1
  prev=segA
  for k in 0 1 2 3 4; do
    nxt=$([ $k -lt 4 ] && echo "x$k" || echo "xs")
    echo "[$prev][im$k]xfade=transition=${XFT[$k]}:duration=${XFD[$k]}:offset=$off[$nxt];"
    prev=$nxt
    off=$(py "$off + $SEG")
  done
  # glow on each ding (background section), glitter everywhere, petal wave after each ding, shower on the images
  echo "[xs]eq=brightness='0.055*exp(-mod(t-12.25,8.3)/0.9)*gte(t,12.25)*lt(t,$T1)':eval=frame,format=rgba[xg];"
  echo "[xg][4:v]overlay=0:0:format=auto:shortest=0[xgl];"
  echo "[xgl][5:v]overlay=0:0:format=auto:enable='between(t,12,$T1)'[xpd];"
  echo "[xpd][11:v]overlay=0:0:format=auto:enable='gte(t,$(py "$T1 - 1"))'[xps];"
  echo "[xps][2:v]overlay=0:0:format=auto[xc];"
  echo "[xc]drawtext=fontfile=$FONT:fontsize=38:fontcolor=0xFFE0A0@0.80:shadowcolor=black@0.6:shadowx=2:shadowy=2:x=(w-text_w)/2:y=h-78:enable='gte(t,4.9)':text='%{eif\:clip(floor((t-5)/8.3)+1\,1\,108)\:d} / 108',"
  echo "fade=t=in:st=0:d=3,fade=t=out:st=$(py "$TOTAL - 6"):d=6,format=yuv420p[v]"
} > "$SCRIPT"

ARGS=(-loop 1 -framerate 30 -t "$(py "$T1 + ${XFD[0]}")" -i "$A/bg.png"   # 0
      -loop 1 -framerate 30 -i "$A/om.png"                                 # 1
      -loop 1 -framerate 30 -i "$A/caption.png"                            # 2
      -loop 1 -framerate 30 -i "$A/shade.png"                              # 3
      -stream_loop -1 -i "$A/glitter.mov"                                  # 4
      -stream_loop -1 -i "$A/petals_ding.mov")                             # 5
for k in 0 1 2 3 4; do                                                     # 6-10
  ARGS+=(-loop 1 -framerate 30 -t "${DUR[$k]}" -i "$A/hi/${IMG[$k]%.*}.png")
done
ARGS+=(-stream_loop -1 -i "$A/petals_shower.mov"                           # 11
       -i "$P/audio/final/om_saravana_bhava_108_final.wav")                # 12

ffmpeg -hide_banner -loglevel warning -stats -y "${ARGS[@]}" -filter_complex_script "$SCRIPT" \
  -map "[v]" -map 12:a -t "$TOTAL" -c:v libx264 -preset medium -crf 18 -r 30 \
  -c:a aac -b:a 256k -movflags +faststart "$OUT"
cp "$SCRIPT" "${OUT%.mp4}.ffgraph"
rm -f "$SCRIPT"
