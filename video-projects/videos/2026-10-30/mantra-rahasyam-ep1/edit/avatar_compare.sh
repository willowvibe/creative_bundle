#!/usr/bin/env bash
# Side-by-side of the Kling Avatar Pro and OmniHuman 2.0 takes of the same still + line.
# usage: edit/avatar_compare.sh <visual>   (default v15) -> edit/avatar_test/<visual>_compare.mp4
set -euo pipefail
cd "$(dirname "$0")/.."
V="${1:-v15}"
A="edit/avatar_test/${V}_kling_avatar_pro.mp4"
B="edit/avatar_test/${V}_omnihuman_2.mp4"
F=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
# The two takes run different lengths (Kling 4.27 s, OmniHuman 5.0 s on the same padded audio): hold the
# shorter one's last frame and pad its audio so neither side is cut.
ffmpeg -y -v error -i "$A" -i "$B" -filter_complex "\
[0:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1,tpad=stop_mode=clone:stop_duration=1,\
drawtext=fontfile=$F:text='Kling Avatar 2.0 Pro  (14 cr)':x=16:y=16:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=8[a];\
[1:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1,\
drawtext=fontfile=$F:text='OmniHuman 2.0  (21 cr/s)':x=16:y=16:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=8[b];\
[a][b]hstack=inputs=2[v]" \
  -map "[v]" -map 0:a -af apad -c:v libx264 -crf 20 -pix_fmt yuv420p -c:a aac -t 5.21 \
  "edit/avatar_test/${V}_compare.mp4"
echo "edit/avatar_test/${V}_compare.mp4"
