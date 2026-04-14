#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import release_metadata


REPO_ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER_SHA = "0" * 64


def read_sha256(path: Path | None) -> str:
    if path is None:
        return PLACEHOLDER_SHA

    sha = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SystemExit(f"Invalid sha256 value in {path}: {sha}")
    return sha


def extract_existing_commit(path: Path) -> str | None:
    if not path.exists():
        return None

    text = path.read_text(encoding="utf-8")
    match = re.search(r'^notes = "Built from GCC .* commit ([0-9a-f]+)"$', text, re.MULTILINE)
    if not match:
        return None
    return match.group(1)


def build_manifest(meta: dict[str, str], sha256_value: str) -> str:
    return f'''name = "gnat_native"
version = "{meta["CRATE_VERSION"]}"
provides = ["gnat={meta["CRATE_VERSION"]}"]
description = "The GNAT Ada compiler with backports"
website = "https://github.com/Prunt3D/gcc"
maintainers = ["Liam Powell <liam@liampwll.com>"]
maintainers-logins = ["liampwll"]
licenses = "GPL-3.0-or-later AND GPL-3.0-or-later WITH GCC-exception-3.1"
auto-gpr-with = false
notes = "Built from GCC {meta["GCC_VERSION"]} commit {meta["GCC_COMMIT"]}"

[configuration]
disabled = true

[environment."case(os)".linux]
PATH.prepend = "${{CRATE_ROOT}}/local/bin"
LIBRARY_PATH.prepend = "${{CRATE_ROOT}}/local/lib64"
LD_LIBRARY_PATH.prepend = "${{CRATE_ROOT}}/local/lib64"
LD_RUN_PATH.prepend = "${{CRATE_ROOT}}/local/lib64"

[origin."case(os)".linux."case(host-arch)".x86-64]
binary = true
url = "{meta["RELEASE_URL"]}"
hashes = ["sha256:{sha256_value}"]
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Alire manifest for the packaged GNAT build.")
    parser.add_argument("--repo-slug", help="Override the GitHub repository slug used in release URLs.")
    parser.add_argument("--sha256-file", type=Path, help="Path to the sha256 file produced for the packaged tarball.")
    args = parser.parse_args()

    meta = release_metadata.metadata(args.repo_slug)
    manifest_path = REPO_ROOT / meta["MANIFEST_PATH"]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    existing_commit = extract_existing_commit(manifest_path)
    if existing_commit and existing_commit != meta["GCC_COMMIT"]:
        raise SystemExit(
            "crate_version reuse detected: "
            f"{meta['CRATE_VERSION']} already points at GCC commit {existing_commit}, "
            f"not {meta['GCC_COMMIT']}"
        )

    manifest_path.write_text(
        build_manifest(meta, read_sha256(args.sha256_file)),
        encoding="utf-8",
    )
    print(manifest_path.relative_to(REPO_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
