#!/usr/bin/env python3
"""Consume the actual binary archive in a fresh Alire workspace."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import libadalang_release as release


def fingerprints(root: Path) -> dict:
    result = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".a", ".ali", ".gpr", ".ads", ".adb"}:
            with path.open("rb") as stream:
                result[str(path.relative_to(root))] = (
                    path.stat().st_mtime_ns, hashlib.file_digest(stream, "sha256").hexdigest())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--consumer-sources", type=Path,
                        help="Also build Prunt's config_codegen sources against the bundle")
    args = parser.parse_args()
    archive = args.archive.resolve()
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    meta = release.metadata()
    with tempfile.TemporaryDirectory(prefix="libadalang-smoke-") as temporary:
        root = Path(temporary)
        index = root / "index"
        manifest = index / meta["MANIFEST_PATH"]
        manifest.parent.mkdir(parents=True)
        (index / "index/index.toml").write_text('version = "1.4.0"\n')
        manifest.write_text(release.manifest({
            meta["PACKAGE_ARCH"]: (archive.as_uri(), digest),
        }))
        consumer = root / "consumer"
        (consumer / "src").mkdir(parents=True)
        shutil.copy2(release.ROOT / "tests/libadalang/smoke.adb", consumer / "src/smoke.adb")
        shutil.copy2(release.ROOT / "tests/libadalang/smoke.gpr", consumer / "smoke.gpr")
        (consumer / "alire.toml").write_text(f'''name = "smoke"
description = "Test binary distribution"
version = "0.0.0"
licenses = "MIT"
[[depends-on]]
libadalang_prebuilt = "={meta['CRATE_VERSION']}"
libadalang = "={release.exact_version('libadalang')}"
vss_text = "={release.exact_version('vss_text')}"
gprbuild = "={release.exact_version('gprbuild')}"
''')

        name = f"prunt_binary_smoke_{os.getpid()}"
        def alr(*arguments, **kwargs):
            return release.run("alr", "-n", *arguments, cwd=consumer, **kwargs)

        alr("index", f"--add={index}", f"--name={name}", "--before=community")
        try:
            alr("update")
            lock = release.read_toml(consumer / "alire/alire.lock")
            for state in lock["solution"]["state"]:
                if state["fulfilment"] != "solved":
                    raise ValueError(f"Unresolved or source-pinned smoke dependency: {state}")
                crate = state["release"]["name"]
                if crate in release.LIBRARIES:
                    raise ValueError(f"Bundled dependency resolved to source crate: {crate}")
            environment = release.alire_environment(consumer)
            bundle = Path(environment["LIBADALANG_PREBUILT_ALIRE_PREFIX"])
            before = fingerprints(bundle)
            alr("build")
            # Run directly, as Prunt's prebuild does, with no library-specific
            # Alire environment. The Ada libraries must be linked statically.
            result = release.run(consumer / "bin/smoke", capture_output=True, text=True)
            if result.stdout.strip() != "Static Libadalang OK":
                raise ValueError(result.stdout)
            dynamic = subprocess.run(["ldd", consumer / "bin/smoke"], check=False,
                                     capture_output=True, text=True)
            if any(name in dynamic.stdout for name in (
                "libadalang", "liblangkit", "libgnatcoll", "libxmlada", "libvss", "libgpr")):
                raise ValueError(f"Unexpected Ada shared dependency: {dynamic.stdout}")
            if args.consumer_sources:
                for source in args.consumer_sources.resolve().glob("*.ad[bs]"):
                    shutil.copy2(source, consumer / "src" / source.name)
                project = (consumer / "smoke.gpr").read_text().replace(
                    '("smoke.adb")', '("config_codegen.adb", "package_subprogram_audit.adb")')
                (consumer / "smoke.gpr").write_text(project)
                alr("build", "--", "-cargs:Ada", "-gnat2022", "-gnatW8")
                release.run(consumer / "bin/config_codegen", "--help",
                            stdout=subprocess.DEVNULL)
            if fingerprints(bundle) != before:
                raise ValueError("Consumer build modified/recompiled the binary bundle")
            print("Binary archive resolved, linked and ran without rebuilding its Ada libraries.")
        finally:
            alr("index", f"--del={name}")


if __name__ == "__main__":
    main()
