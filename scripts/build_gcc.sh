#!/usr/bin/env bash
set -euo pipefail

JOBS="$(nproc)"
SOURCE_DIR="./gcc"
WORK_DIR="./build"
STAGE_DIR="./stage"
CONFIGURE_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --jobs)
      JOBS="$2"
      shift 2
      ;;
    --source-dir)
      SOURCE_DIR="$2"
      shift 2
      ;;
    --work-dir)
      WORK_DIR="$2"
      shift 2
      ;;
    --stage-dir)
      STAGE_DIR="$2"
      shift 2
      ;;
    --configure-only)
      CONFIGURE_ONLY=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

SOURCE_DIR="$(readlink -f "$SOURCE_DIR")"
WORK_DIR="$(readlink -m "$WORK_DIR")"
STAGE_DIR="$(readlink -m "$STAGE_DIR")"

if [[ ! -x "$SOURCE_DIR/configure" ]]; then
  echo "GCC source tree not found at $SOURCE_DIR" >&2
  exit 1
fi

if [[ -n "${ALIRE_GNAT_BIN:-}" ]]; then
  export PATH="${ALIRE_GNAT_BIN}:$PATH"
fi

unset ADA_INCLUDE_PATH
unset ADA_OBJECT_PATH

for tool in gnat gnatmake gnatbind; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Required Ada bootstrap tool '$tool' is not on PATH" >&2
    exit 1
  fi
done

rm -rf "$WORK_DIR" "$STAGE_DIR"
mkdir -p "$WORK_DIR" "$STAGE_DIR"

pushd "$WORK_DIR" >/dev/null
CONFIGURE_PLATFORM_ARGS=()
if [[ "${PACKAGE_ARCH:-}" == "aarch64" ]]; then
  CONFIGURE_PLATFORM_ARGS+=(
    --build=aarch64-linux-gnu
    --host=aarch64-linux-gnu
    --target=aarch64-linux-gnu
  )
fi

"$SOURCE_DIR/configure" \
  "${CONFIGURE_PLATFORM_ARGS[@]}" \
  --enable-languages=c,ada,fortran \
  --disable-multilib \
  --disable-bootstrap \
  --enable-checking=yes,extra,rtl

if [[ "$CONFIGURE_ONLY" == "1" ]]; then
  popd >/dev/null
  exit 0
fi

make "-j${JOBS}"
make "-j${JOBS}" "DESTDIR=${STAGE_DIR}" install
popd >/dev/null
