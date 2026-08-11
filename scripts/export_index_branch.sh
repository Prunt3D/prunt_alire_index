#!/usr/bin/env bash
set -euo pipefail

BRANCH="alire-index"
PUSH=0
KEEP_WORK_DIR=0
WORK_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)
      BRANCH="$2"
      shift 2
      ;;
    --push)
      PUSH=1
      shift
      ;;
    --work-dir)
      WORK_DIR="$2"
      shift 2
      ;;
    --keep-work-dir)
      KEEP_WORK_DIR=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INDEX_DIR="${REPO_ROOT}/index"
REMOTE_URL="$(git -C "${REPO_ROOT}" remote get-url origin 2>/dev/null || true)"

if [[ -n "${GITHUB_TOKEN:-}" && -n "${REPO_SLUG:-}" ]]; then
  REMOTE_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${REPO_SLUG}.git"
fi

if [[ ! -f "${INDEX_DIR}/index.toml" ]]; then
  echo "Index metadata not found at ${INDEX_DIR}/index.toml" >&2
  exit 1
fi

CREATED_WORK_DIR=0
if [[ -z "${WORK_DIR}" ]]; then
  WORK_DIR="$(mktemp -d)"
  CREATED_WORK_DIR=1
else
  WORK_DIR="$(readlink -m "${WORK_DIR}")"
  rm -rf "${WORK_DIR}"
  mkdir -p "${WORK_DIR}"
fi

cleanup() {
  if [[ "${CREATED_WORK_DIR}" == "1" && "${KEEP_WORK_DIR}" == "0" ]]; then
    rm -rf "${WORK_DIR}"
  fi
}
trap cleanup EXIT

git init --initial-branch="${BRANCH}" "${WORK_DIR}" >/dev/null
git -C "${WORK_DIR}" config user.name "${GIT_AUTHOR_NAME:-github-actions[bot]}"
git -C "${WORK_DIR}" config user.email "${GIT_AUTHOR_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"

if [[ -n "${REMOTE_URL}" ]]; then
  git -C "${WORK_DIR}" remote add origin "${REMOTE_URL}"
fi

if [[ -n "${REMOTE_URL}" ]] && git ls-remote --exit-code --heads origin "${BRANCH}" >/dev/null 2>&1; then
  git -C "${WORK_DIR}" fetch --depth=1 origin "${BRANCH}" >/dev/null
  git -C "${WORK_DIR}" checkout -B "${BRANCH}" FETCH_HEAD >/dev/null
else
  git -C "${WORK_DIR}" checkout --orphan "${BRANCH}" >/dev/null
fi

# Overlay the generated index on the published branch instead of replacing the
# branch contents.  Older crate-version manifests live only on the index branch
# and must remain available when a new version is published.
mkdir -p "${WORK_DIR}/index"
cp -a "${INDEX_DIR}/." "${WORK_DIR}/index/"
cat > "${WORK_DIR}/README.md" <<EOF
# Prunt Alire Index

This branch is published automatically from the main development branch.

It contains only the Alire index so consumers do not need to clone the GCC
submodule used to build the release artifacts.
EOF

git -C "${WORK_DIR}" add README.md index

if git -C "${WORK_DIR}" diff --cached --quiet; then
  echo "Index branch already up to date"
else
  git -C "${WORK_DIR}" commit -m "Update Alire index for ${CRATE_VERSION:-unknown}" >/dev/null
fi

if [[ "${PUSH}" == "1" ]]; then
  if [[ -z "${REMOTE_URL}" ]]; then
    echo "Cannot push index branch without an 'origin' remote" >&2
    exit 1
  fi
  git -C "${WORK_DIR}" push origin "HEAD:refs/heads/${BRANCH}" >/dev/null
fi

echo "${WORK_DIR}"
