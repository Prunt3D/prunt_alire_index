#!/usr/bin/env python3
"""Build and package the static Ada library closure, never the compiler."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

import release_metadata


ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT / "libadalang"
LIBRARIES = (
    "libadalang", "langkit_support", "adasat", "prettier_ada", "vss_text",
    "gnatcoll", "gnatcoll_minimal", "gnatcoll_projects", "gnatcoll_gmp",
    "gnatcoll_iconv", "libgpr", "libgpr2", "xmlada",
)
STATIC_VARIABLES = (
    "LIBRARY_TYPE", "LIBADALANG_LIBRARY_TYPE", "LANGKIT_SUPPORT_LIBRARY_TYPE",
    "PRETTIER_ADA_LIBRARY_TYPE", "VSS_LIBRARY_TYPE",
)


def read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def dependencies() -> dict[str, str]:
    return {name: version for table in read_toml(INPUTS / "alire.toml")["depends-on"]
            for name, version in table.items()}


def exact_version(name: str) -> str:
    value = dependencies()[name]
    if not re.fullmatch(r"=\d+\.\d+\.\d+", value):
        raise ValueError(f"{name} must have an exact version, got {value!r}")
    return value[1:]


def metadata(repo_slug: str | None = None, arch: str | None = None) -> dict[str, str]:
    version = read_toml(INPUTS / "release.toml")["crate_version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Invalid bundle version: {version!r}")
    arch = release_metadata.normalize_package_arch(arch or os.uname().machine)
    repo = release_metadata.resolve_repo_slug(repo_slug)
    tag = f"libadalang-static-{version}"
    asset = f"libadalang-static-{arch}-linux-{version}.tar.gz"
    return {
        "CRATE_VERSION": version,
        "GNAT_VERSION": exact_version("gnat_native"),
        "PACKAGE_ARCH": arch,
        "REPO_SLUG": repo,
        "INDEX_BRANCH": "alire-index",
        "RELEASE_TAG": tag,
        "RELEASE_NAME": f"Libadalang static {version}",
        "ASSET_NAME": asset,
        "RELEASE_URL": f"https://github.com/{repo}/releases/download/{tag}/{asset}",
        "MANIFEST_PATH": f"index/li/libadalang_prebuilt/libadalang_prebuilt-{version}.toml",
    }


def manifest(origins: dict[str, tuple[str, str]]) -> str:
    """Origins map package architectures to (URL, SHA256)."""
    meta = metadata()
    provides = [f"{name}={exact_version(name)}" for name in LIBRARIES]
    text = f'''name = "libadalang_prebuilt"
version = "{meta['CRATE_VERSION']}"
description = "Prebuilt static Libadalang and its Ada dependencies for Prunt"
website = "https://github.com/Prunt3D/prunt_alire_index"
maintainers = ["Liam Powell <liam@liampwll.com>"]
maintainers-logins = ["liampwll"]
licenses = "Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND GPL-3.0-or-later WITH GCC-exception-3.1"
provides = {json.dumps(provides)}
project-files = ["share/gpr/libadalang.gpr", "share/gpr/langkit_support.gpr", "share/gpr/vss_text.gpr"]
notes = "Static Ada libraries built with Prunt GNAT {meta['GNAT_VERSION']}; requires system GMP and glibc."

[configuration]
disabled = true

[[depends-on]]
gnat_native = "={meta['GNAT_VERSION']}"
gnat = "={meta['GNAT_VERSION']}"
libgmp = "*"

# Prevent a second copy of any bundled Ada library in the same solution.
[[forbids]]
'''
    text += "".join(f'{name} = "*"\n' for name in LIBRARIES)
    text += '\n[available."case(os)"]\nlinux = true\n"..." = false\n'
    for arch, (url, digest) in origins.items():
        host = release_metadata.PACKAGE_ARCHES[arch]
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or digest == "0" * 64:
            raise ValueError(f"Invalid SHA256 for {arch}: {digest!r}")
        text += f'''
[origin."case(os)".linux."case(host-arch)".{host}]
binary = true
url = {json.dumps(url)}
hashes = ["sha256:{digest}"]
'''
    return text


def run(*args: str, **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def alire_environment(crate: Path) -> dict[str, str]:
    result = subprocess.run(
        ["alr", "-n", f"--chdir={crate}", "exec", "--", "env", "-0"],
        check=True, capture_output=True,
    )
    return dict(item.decode().split("=", 1) for item in result.stdout.split(b"\0")
                if b"=" in item)


def verify_inputs(crate: Path) -> None:
    states = {state["crate"]: state
              for state in read_toml(crate / "alire/alire.lock")["solution"]["state"]}
    pins = {name: value for table in read_toml(INPUTS / "alire.toml").get("pins", [])
            for name, value in table.items()}
    for name in (*LIBRARIES, "gnat_native", "gnat"):
        state = states[name]
        if name in pins:
            if (state.get("link", {}).get("commit") != pins[name]["commit"]
                    or state.get("link", {}).get("url") != pins[name]["url"]):
                raise ValueError(f"Build does not use the required source revision of {name}")
        elif (state.get("release", {}).get("name") != ("gnat_native" if name == "gnat" else name)
              or state.get("release", {}).get("version") != exact_version(name)):
            raise ValueError(f"Build does not use the required version of {name}")


def install(crate: Path, stage: Path) -> None:
    if stage.exists():
        raise ValueError(f"Stage already exists; use a new directory: {stage}")
    environment = alire_environment(crate)
    verify_inputs(crate)
    # -f permits XML/Ada's Install.Artifacts to copy its aggregate project over
    # the generated version. Every invocation starts with an empty prefix.
    run("alr", "-n", f"--chdir={crate}", "exec", "--", "gprinstall",
        "-P", "libadalang.gpr", "-r", "-p", "-f", "--mode=dev", "--no-build-var",
        f"--prefix={stage}", *(f"-X{var}=static" for var in STATIC_VARIABLES))

    # XML/Ada installs this source-free aggregate as an artifact, bypassing
    # gprinstall's usual Externally_Built attribute generation.
    aggregate = stage / "share/gpr/xmlada.gpr"
    aggregate.write_text(aggregate.read_text().replace(
        "project Xmlada is", 'project Xmlada is\n   for Externally_Built use "True";'))

    # GPRinstall 25 drops imports of XML/Ada's source-free aggregate, including
    # its transitive library dependencies. Restore those edges for static links.
    for name in ("gpr.gpr", "gpr2.gpr"):
        project = stage / "share/gpr" / name
        content = project.read_text()
        if not re.search(r'with\s+"xmlada(?:\.gpr)?"', content, re.I):
            project.write_text('with "xmlada.gpr";\n' + content)

    for project in (stage / "share/gpr").glob("*.gpr"):
        content = project.read_text()
        if not re.search(r'for\s+Externally_Built\s+use\s+"True"', content, re.I):
            raise ValueError(f"Not externally built: {project}")
        if re.search(r'for\s+Library_Kind\s+use\s+"(?!static")', content, re.I):
            raise ValueError(f"Not a static library: {project}")
        if str(crate) in content or str(stage) in content or "ALIRE_PREFIX" in content:
            raise ValueError(f"Build-tree reference in installed project: {project}")

    if not list(stage.rglob("libadalang.a")) or not list(stage.rglob("libadalang.ali")):
        raise ValueError("Libadalang library or ALI metadata missing")
    if list(stage.rglob("*.so*")):
        raise ValueError("Unexpected shared library in static bundle")

    provenance = stage / "share/libadalang_prebuilt"
    provenance.mkdir(parents=True)
    shutil.copy2(INPUTS / "alire.toml", provenance / "build-inputs.toml")
    shutil.copy2(INPUTS / "release.toml", provenance / "release.toml")
    shutil.copy2(crate / "alire/alire.lock", provenance / "build.lock")
    for name in LIBRARIES:
        source = Path(environment[f"{name.upper()}_ALIRE_PREFIX"])
        # Preserve notices in subdirectories too (e.g. vendored C components).
        for directory, dirs, files in os.walk(source):
            dirs[:] = [d for d in dirs if d not in {".git", "obj", "lib", "alire", ".build"}]
            for filename in files:
                if filename.lower().startswith(("license", "copying", "notice")):
                    path = Path(directory) / filename
                    dest = stage / "share/licenses" / name / path.relative_to(source)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dest)


def package(stage: Path, dist: Path, arch: str) -> Path:
    dist.mkdir(parents=True, exist_ok=True)
    archive = dist / metadata(arch=arch)["ASSET_NAME"]
    # One enclosing directory is required by Alire's archive extraction.
    run("tar", "--sort=name", "--mtime=UTC 1970-01-01", "--owner=0", "--group=0",
        "--numeric-owner", "-czf", archive, "-C", stage.parent, stage.name)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(digest + "\n")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    meta_parser = sub.add_parser("metadata")
    meta_parser.add_argument("--repo-slug")
    meta_parser.add_argument("--format", choices=("json", "github-env"), default="json")
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--stage", type=Path, default=ROOT / "stage/libadalang")
    build_parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    build_parser.add_argument("--reuse-build", type=Path,
                              help="Package an existing matching Alire build (local development only)")
    gen_parser = sub.add_parser("manifest")
    gen_parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    gen_parser.add_argument("--repo-slug")
    args = parser.parse_args()
    if args.command == "metadata":
        values = metadata(args.repo_slug)
        if args.format == "github-env":
            release_metadata.emit_github_env(values)
        else:
            print(json.dumps(values, indent=2))
    elif args.command == "build":
        crate = args.reuse_build.resolve() if args.reuse_build else INPUTS
        if not args.reuse_build:
            run("alr", "-n", f"--chdir={crate}", "build", "--release")
        install(crate, args.stage.resolve())
        package(args.stage.resolve(), args.dist.resolve(), os.uname().machine)
    else:
        origins = {}
        for arch in release_metadata.PACKAGE_ARCHES:
            meta = metadata(args.repo_slug, arch)
            archive = args.dist / meta["ASSET_NAME"]
            with archive.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            recorded = archive.with_name(archive.name + ".sha256").read_text().strip()
            if recorded != digest:
                raise ValueError(f"Checksum mismatch: {archive}")
            origins[arch] = (meta["RELEASE_URL"], digest)
        output = ROOT / metadata()["MANIFEST_PATH"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(manifest(origins))
        print(output)


if __name__ == "__main__":
    main()
