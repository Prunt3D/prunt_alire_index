#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_FILE = REPO_ROOT / "release.toml"
GCC_DIR = REPO_ROOT / "gcc"
INDEX_BRANCH = "alire-index"
PLACEHOLDER_REPO = "REPO_OWNER/REPO_NAME"


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd or REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def load_release_config() -> dict[str, str]:
    with RELEASE_FILE.open("rb") as stream:
        return tomllib.load(stream)


def resolve_repo_slug(explicit_repo_slug: str | None) -> str:
    if explicit_repo_slug:
        return explicit_repo_slug

    env_slug = os.environ.get("GITHUB_REPOSITORY")
    if env_slug:
        return env_slug

    try:
        remote = git("remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        return PLACEHOLDER_REPO

    github_patterns = (
        re.compile(r"^https://github\.com/([^/]+/[^/]+?)(?:\.git)?$"),
        re.compile(r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$"),
        re.compile(r"^ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?$"),
    )
    for pattern in github_patterns:
        match = pattern.match(remote)
        if match:
            return match.group(1)

    return PLACEHOLDER_REPO


def metadata(repo_slug: str | None = None) -> dict[str, str]:
    release = load_release_config()
    crate_version = release["crate_version"]
    gcc_commit = git("-C", str(GCC_DIR), "rev-parse", "HEAD")
    gcc_version = crate_version
    repo_slug = resolve_repo_slug(repo_slug)
    asset_name = f"gnat-x86_64-linux-{crate_version}.tar.gz"
    release_tag = f"gnat-{crate_version}"
    return {
        "CRATE_VERSION": crate_version,
        "GCC_COMMIT": gcc_commit,
        "GCC_VERSION": gcc_version,
        "INDEX_BRANCH": INDEX_BRANCH,
        "REPO_SLUG": repo_slug,
        "ASSET_NAME": asset_name,
        "ASSET_SHA_NAME": f"{asset_name}.sha256",
        "RELEASE_TAG": release_tag,
        "RELEASE_NAME": f"GNAT Native {crate_version} (GCC {gcc_version})",
        "RELEASE_URL": f"https://github.com/{repo_slug}/releases/download/{release_tag}/{asset_name}",
        "MANIFEST_PATH": f"index/gn/gnat_native/gnat_native-{crate_version}.toml",
    }


def emit_shell(values: dict[str, str]) -> None:
    for key, value in values.items():
        print(f"{key}={shlex.quote(value)}")


def emit_github_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        print(f"{key}={value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit release metadata for scripts and CI.")
    parser.add_argument("--repo-slug", help="Override the GitHub repository slug used in release URLs.")
    parser.add_argument(
        "--format",
        choices=("json", "shell", "github-env"),
        default="json",
        help="Output format.",
    )
    args = parser.parse_args()

    values = metadata(args.repo_slug)

    if args.format == "json":
        json.dump(values, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    elif args.format == "shell":
        emit_shell(values)
    else:
        emit_github_env(values)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
