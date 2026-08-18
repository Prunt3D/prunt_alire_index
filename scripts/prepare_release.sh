#!/usr/bin/env bash
set -euo pipefail

GCC_REMOTE="origin"
GCC_BRANCH="releases/gcc-16"
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: scripts/prepare_release.sh [--dry-run]

Fetch the latest Prunt GCC 16 fork commit, increment the Alire crate version,
and commit both changes. The resulting commit is not pushed.

Options:
  --dry-run  Fetch and report the proposed release without changing files.
  -h, --help Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
GCC_DIR="$REPO_ROOT/gcc"
RELEASE_FILE="$REPO_ROOT/release.toml"

cd "$REPO_ROOT"

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo "The working tree must be clean before preparing a release." >&2
  exit 1
fi

if ! git -C "$GCC_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "The GCC submodule is not initialized; run 'git submodule update --init gcc'." >&2
  exit 1
fi

current_gcc_commit="$(git -C "$GCC_DIR" rev-parse HEAD)"

echo "Fetching $GCC_REMOTE/$GCC_BRANCH..."
git -C "$GCC_DIR" fetch "$GCC_REMOTE" "$GCC_BRANCH"
latest_gcc_commit="$(git -C "$GCC_DIR" rev-parse FETCH_HEAD)"

if [[ "$current_gcc_commit" == "$latest_gcc_commit" ]]; then
  echo "GCC is already at the latest $GCC_BRANCH commit ($current_gcc_commit)."
  exit 0
fi

if git -C "$GCC_DIR" merge-base --is-ancestor "$current_gcc_commit" "$latest_gcc_commit"; then
  gcc_update_kind="fast-forward"
else
  gcc_update_kind="divergent history"
fi

gcc_base_version="$(git -C "$GCC_DIR" show "$latest_gcc_commit:gcc/BASE-VER")"
version_output="$(
  python3 - "$RELEASE_FILE" "$gcc_base_version" <<'PY'
import re
import sys
import tomllib
from pathlib import Path

release_file = Path(sys.argv[1])
gcc_version = sys.argv[2]

with release_file.open("rb") as stream:
    crate_version = tomllib.load(stream).get("crate_version", "")

match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", crate_version)
if not match:
    raise SystemExit(f"Invalid crate_version in {release_file}: {crate_version!r}")

gcc_match = re.fullmatch(r"(\d+)\.(\d+)\.\d+", gcc_version)
if not gcc_match:
    raise SystemExit(f"Invalid GCC BASE-VER: {gcc_version!r}")

crate_series = tuple(map(int, match.group(1, 2)))
gcc_series = tuple(map(int, gcc_match.group(1, 2)))
if gcc_series < crate_series:
    raise SystemExit(
        f"Refusing to move from crate version {crate_version} back to GCC {gcc_version}"
    )

if gcc_series == crate_series:
    package_revision = int(match.group(3)) + 1
else:
    package_revision = 1001

next_version = f"{gcc_series[0]}.{gcc_series[1]}.{package_revision}"
print(crate_version)
print(next_version)
PY
)"
readarray -t versions <<<"$version_output"
current_version="${versions[0]}"
next_version="${versions[1]}"

echo "GCC:    ${current_gcc_commit:0:12} -> ${latest_gcc_commit:0:12}"
echo "History: $gcc_update_kind"
echo "Release: $current_version -> $next_version"

if [[ "$DRY_RUN" == "1" ]]; then
  exit 0
fi

# A detached checkout is deliberate: the parent repository records the exact
# submodule commit, while the local fork branch remains untouched.
git -C "$GCC_DIR" checkout --detach "$latest_gcc_commit"

python3 - "$RELEASE_FILE" "$current_version" "$next_version" <<'PY'
import sys
from pathlib import Path

release_file = Path(sys.argv[1])
old_version = sys.argv[2]
new_version = sys.argv[3]
old_line = f'crate_version = "{old_version}"'
new_line = f'crate_version = "{new_version}"'
text = release_file.read_text(encoding="utf-8")
if text.count(old_line) != 1:
    raise SystemExit(f"Expected exactly one {old_line!r} line in {release_file}")
release_file.write_text(text.replace(old_line, new_line), encoding="utf-8")
PY

python3 scripts/release_metadata.py --package-arch x86_64 >/dev/null
python3 scripts/release_metadata.py --package-arch aarch64 >/dev/null
git diff --check

git add gcc release.toml
git commit -m "Release $next_version"

echo "Prepared release $next_version at commit $(git rev-parse --short HEAD)."
echo "Review the commit, then push it to start the release workflow."
