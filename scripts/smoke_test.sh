#!/usr/bin/env bash
set -euo pipefail

STAGE_DIR="./stage"
TEST_DIR="./smoke-test"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage-dir)
      STAGE_DIR="$2"
      shift 2
      ;;
    --test-dir)
      TEST_DIR="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

STAGE_DIR="$(readlink -f "$STAGE_DIR")"
TEST_DIR="$(readlink -m "$TEST_DIR")"

export PATH="$STAGE_DIR/usr/local/bin:$PATH"
export LIBRARY_PATH="$STAGE_DIR/usr/local/lib64:$STAGE_DIR/usr/local/lib:${LIBRARY_PATH:-}"
export LD_LIBRARY_PATH="$STAGE_DIR/usr/local/lib64:$STAGE_DIR/usr/local/lib:${LD_LIBRARY_PATH:-}"
export LD_RUN_PATH="$STAGE_DIR/usr/local/lib64:$STAGE_DIR/usr/local/lib:${LD_RUN_PATH:-}"

rm -rf "$TEST_DIR"
mkdir -p "$TEST_DIR"

gnat --version
gnatmake --version
gcc --version
gfortran --version

cat > "$TEST_DIR/hello.c" <<'EOF'
#include <stdio.h>

int main(void) {
    puts("Hello, C!");
    return 0;
}
EOF

pushd "$TEST_DIR" >/dev/null
gcc -o hello_c hello.c
c_output="$(./hello_c)"
popd >/dev/null

if [[ "$c_output" != "Hello, C!" ]]; then
  echo "Unexpected C smoke test output: $c_output" >&2
  exit 1
fi

cat > "$TEST_DIR/hello.adb" <<'EOF'
with Ada.Text_IO;

procedure Hello is
begin
   Ada.Text_IO.Put_Line ("Hello, World!");
end Hello;
EOF

pushd "$TEST_DIR" >/dev/null
gnatmake -q hello.adb
output="$(./hello)"
popd >/dev/null

if [[ "$output" != "Hello, World!" ]]; then
  echo "Unexpected smoke test output: $output" >&2
  exit 1
fi

cat > "$TEST_DIR/hello.f90" <<'EOF'
program hello_fortran
  implicit none
  print '(A)', 'Hello, Fortran!'
end program hello_fortran
EOF

pushd "$TEST_DIR" >/dev/null
gfortran -o hello_fortran hello.f90
fortran_output="$(./hello_fortran)"
popd >/dev/null

if [[ "$fortran_output" != "Hello, Fortran!" ]]; then
  echo "Unexpected Fortran smoke test output: $fortran_output" >&2
  exit 1
fi
