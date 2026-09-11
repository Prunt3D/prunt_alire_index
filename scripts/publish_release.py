#!/usr/bin/env python3
"""Publish archives immutably; retry failed publication without rebuilding."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import quote


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True)


def get_release(repo: str, tag: str) -> dict | None:
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/releases/tags/{quote(tag, safe='')}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return json.loads(result.stdout)
    if "(HTTP 404)" in result.stderr:
        # The tag endpoint omits drafts. Include them so an interrupted upload
        # resumes its draft instead of creating another release for the tag.
        pages = json.loads(gh("api", f"repos/{repo}/releases?per_page=100",
                              "--paginate", "--slurp"))
        matches = [release for page in pages for release in page if release["tag_name"] == tag]
        if len(matches) > 1:
            raise ValueError(f"Multiple releases found for {tag}")
        return matches[0] if matches else None
    raise RuntimeError(result.stderr)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def publish(repo: str, tag: str, title: str, notes: str, target: str, dist: Path) -> None:
    archives = sorted(dist.glob("*.tar.gz"))
    if not archives:
        raise ValueError(f"No archives in {dist}")
    files = []
    for archive in archives:
        checksum = archive.with_name(archive.name + ".sha256")
        if digest(archive) != checksum.read_text().strip():
            raise ValueError(f"Checksum mismatch: {archive}")
        files.extend((archive, checksum))
    release = get_release(repo, tag)
    if release is None:
        gh("release", "create", tag, "--repo", repo, "--target", target,
           "--title", title, "--notes", notes, "--draft")
        release = {"assets": [], "draft": True}
    existing = {asset["name"] for asset in release["assets"]}
    missing = []
    with tempfile.TemporaryDirectory(prefix="release-verification-") as temporary:
        for path in files:
            if path.name not in existing:
                missing.append(str(path))
                continue
            # On retry, verify bytes rather than silently replacing an existing
            # asset or assuming that a same-named asset is the same build.
            gh("release", "download", tag, "--repo", repo, "--pattern", path.name,
               "--dir", temporary)
            if digest(Path(temporary) / path.name) != digest(path):
                raise ValueError(f"Refusing to overwrite changed release asset: {path.name}")
    if missing:
        gh("release", "upload", tag, *missing, "--repo", repo)
    if release["draft"]:
        gh("release", "edit", tag, "--repo", repo, "--draft=false")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), required=False)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--target", default=os.environ.get("GITHUB_SHA", "master"))
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--check-unpublished", action="store_true")
    args = parser.parse_args()
    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required")
    if args.check_unpublished:
        if get_release(args.repo, args.tag) is not None:
            raise SystemExit(f"Release {args.tag} already exists. Use a new version for changed "
                             "inputs, or re-run only the failed publish job to resume publication.")
    else:
        publish(args.repo, args.tag, args.title, args.notes, args.target, args.dist)


if __name__ == "__main__":
    main()
