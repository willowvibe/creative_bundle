#!/usr/bin/env bash
# Install a static FFmpeg 8.1 build (BtbN/FFmpeg-Builds) and put it on PATH.
#
# The repo calls `ffmpeg`/`ffprobe` from PATH, so this installs the bundle under
# tools/ffmpeg/ and symlinks ffmpeg + ffprobe into ~/.local/bin (which is on
# PATH ahead of /usr/bin on typical setups). Set FFMPEG_BIN_DIR to install the
# symlinks somewhere else (e.g. /usr/local/bin), or FFMPEG_PREFIX to change the
# bundle location.
#
# Idempotent: re-running re-fetches the pinned "latest 8.1" asset and refreshes
# the symlinks. Existing regular files at the symlink targets are backed up.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

ASSET="ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz"
URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/${ASSET}"
PREFIX="${FFMPEG_PREFIX:-$ROOT_DIR/tools/ffmpeg}"
BIN_DIR="${FFMPEG_BIN_DIR:-$HOME/.local/bin}"

echo "Downloading ${ASSET} ..."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fL --retry 3 "$URL" -o "$tmp/$ASSET"

echo "Extracting ..."
tar -xJf "$tmp/$ASSET" -C "$tmp"
extracted="$(find "$tmp" -maxdepth 1 -type d -name 'ffmpeg-*' | head -1)"
if [ -z "$extracted" ]; then
    echo "error: could not find extracted ffmpeg directory" >&2
    exit 1
fi

mkdir -p "$PREFIX"
rm -rf "$PREFIX/bin"
cp -r "$extracted/bin" "$PREFIX/bin"
cp "$extracted/LICENSE.txt" "$PREFIX/LICENSE.txt" 2>/dev/null || true

mkdir -p "$BIN_DIR"
for tool in ffmpeg ffprobe; do
    target="$BIN_DIR/$tool"
    if [ -e "$target" ] && [ ! -L "$target" ]; then
        echo "backing up existing $target -> $target.bak"
        mv "$target" "$target.bak"
    fi
    ln -sf "$PREFIX/bin/$tool" "$target"
done

echo
"$BIN_DIR/ffmpeg" -version | head -1
echo "Installed: $PREFIX/bin/ffmpeg -> $BIN_DIR/ffmpeg"
echo "Ensure $BIN_DIR is on PATH (it usually is). Verify with: ffmpeg -version"
