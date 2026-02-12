#!/usr/bin/env bash
set -euo pipefail

# png_shrink.sh: run pngquant then oxipng on one input PNG
# #
#   This keeps a backup of the original, writes an output file you choose, and
#   then runs lossless optimization after quantization.

# setup:
#   brew install oxipng pngquant

usage() {
  echo "Usage: $0 input.png [output.png] [quality]"
  echo "  output.png defaults to <input>-min.png"
  echo "  quality defaults to 70-90 (pngquant range)"
}

if [[ $# -lt 1 || $# -gt 3 ]]; then
  usage
  exit 2
fi

in="$1"
if [[ ! -f "$in" ]]; then
  echo "Error: input file not found: $in" >&2
  exit 1
fi

base="${in%.*}"
out="${2:-${base}-min.png}"
quality="${3:-70-90}"

command -v pngquant >/dev/null 2>&1 || { echo "Error: pngquant not found in PATH" >&2; exit 1; }
command -v oxipng  >/dev/null 2>&1 || { echo "Error: oxipng not found in PATH" >&2; exit 1; }

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

tmp_png="$tmpdir/quant.png"

pngquant --quality="$quality" --speed 1 --strip --force --output "$tmp_png" "$in"

# Fix is here: use --strip (double dash)
oxipng -o 4 --strip all -i 0 "$tmp_png" >/dev/null

mv -f "$tmp_png" "$out"
echo "Wrote: $out"
