#!/usr/bin/env bash
set -euo pipefail

STAGE_DIR="./stage"
OUTPUT_DIR="./dist"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage-dir)
      STAGE_DIR="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

STAGE_DIR="$(readlink -f "$STAGE_DIR")"
OUTPUT_DIR="$(readlink -m "$OUTPUT_DIR")"

if [[ ! -x "$STAGE_DIR/usr/local/bin/gnat" ]]; then
  echo "Expected staged compiler at $STAGE_DIR/usr/local/bin/gnat" >&2
  exit 1
fi

if [[ ! -x "$STAGE_DIR/usr/local/bin/gfortran" ]]; then
  echo "Expected staged compiler at $STAGE_DIR/usr/local/bin/gfortran" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

eval "$(python3 "$(dirname "$0")/release_metadata.py" --format shell)"

tar \
  --sort=name \
  --mtime='UTC 1970-01-01' \
  --owner=0 \
  --group=0 \
  --numeric-owner \
  -C "$STAGE_DIR" \
  -czf "$OUTPUT_DIR/$ASSET_NAME" \
  .

sha256sum "$OUTPUT_DIR/$ASSET_NAME" | awk '{print $1}' > "$OUTPUT_DIR/$ASSET_SHA_NAME"
