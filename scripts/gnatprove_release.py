#!/usr/bin/env python3
"""Repackage AdaCore's GNATprove around gnat2why and a compiler built from Prunt's GCC.

gnat2why contains a copy of the GNAT front end, and GNATprove runs the GCC it bundles to compute data
representations, so a released GNATprove analyses code with the front end it was built with. Prunt's GCC fork
accepts code which that front end rejects. This script builds gnat2why from the spark2014 submodule against the
gcc submodule's front end, using the Prunt compiler released from that same commit, and puts it and that
compiler into the released GNATprove archive named in gnatprove/release.toml. The driver, Why3, the provers and
SPARKlib are the released ones. Where gnatprove/release.toml names a COLIBRI build for the architecture, the
archive also gets that prover, which GNATprove supports but does not bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request

import release_metadata


ROOT = Path(__file__).resolve().parent.parent
INPUTS = ROOT / "gnatprove"
GCC_DIR = ROOT / "gcc"
SPARK_DIR = ROOT / "spark2014"
GNAT2WHY_DIR = SPARK_DIR / "gnat2why"

# GCC programs of the released archive's libexec/spark/bin which are replaced by the Prunt compiler's. Programs
# named after the target triplet (x86_64-pc-linux-gnu-gcc, ...) are replaced too.
COMPILER_PROGRAMS = ("cpp", "gcc", "gcc-ar", "gcc-nm", "gcc-ranlib", "gnatbind", "gnatls")

# How gnatprove runs COLIBRI. The released gnatprove.conf passes --steplimit, which public COLIBRI releases do
# not have; these are the arguments upstream Why3 uses for them. --get-steps prints the "Steps : N" line which
# Why3's colibri driver reads.
COLIBRI_ARGS = ["--get-steps", "--memlimit", "%m", "%f"]
COLIBRI_ARGS_STEPS = ["--max-steps", "%S"]

# A problem COLIBRI must answer "unsat", to show that its Prolog engine loads.
COLIBRI_CHECK = "(set-logic ALL)\n(declare-fun x () Int)\n(assert (and (> x 3) (< x 2)))\n(check-sat)\n"


def read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def release_config() -> dict:
    return read_toml(INPUTS / "release.toml")


def colibri_release(arch: str) -> dict | None:
    """The COLIBRI build to bundle for a package architecture, if there is one."""
    colibri = release_config().get("colibri")
    if colibri is None or arch not in colibri:
        return None
    return {"version": colibri["version"], "license": colibri["license"], "source": colibri["source"],
            **colibri[arch]}


def exact_version(name: str) -> str:
    dependencies = {name: version for table in read_toml(INPUTS / "alire.toml")["depends-on"]
                    for name, version in table.items()}
    value = dependencies[name]
    if not re.fullmatch(r"=\d+\.\d+\.\d+", value):
        raise ValueError(f"{name} must have an exact version, got {value!r}")
    return value[1:]


def submodule_commit(name: str, checkout: Path) -> str:
    # Publishing needs only the gitlink, not the checkouts.
    if (checkout / ".git").exists():
        return release_metadata.git("-C", str(checkout), "rev-parse", "HEAD")
    return release_metadata.git("rev-parse", f"HEAD:{name}")


def metadata(repo_slug: str | None = None, arch: str | None = None) -> dict[str, str]:
    config = release_config()
    version = config["crate_version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Invalid GNATprove crate version: {version!r}")
    gnat_version = exact_version("gnat_native")
    if version.split(".")[:2] != gnat_version.split(".")[:2]:
        raise ValueError(f"GNATprove {version} is not in the series of the GCC it is built on ({gnat_version})")
    arch = release_metadata.normalize_package_arch(arch or os.environ.get("PACKAGE_ARCH", os.uname().machine))
    repo = release_metadata.resolve_repo_slug(repo_slug)
    tag = f"gnatprove-{version}"
    asset = f"gnatprove-{arch}-linux-{version}.tar.gz"
    return {
        "CRATE_VERSION": version,
        "GNAT_VERSION": gnat_version,
        "GCC_COMMIT": submodule_commit("gcc", GCC_DIR),
        "SPARK_COMMIT": submodule_commit("spark2014", SPARK_DIR),
        "BASE_VERSION": config["base"]["version"],
        "PACKAGE_ARCH": arch,
        "REPO_SLUG": repo,
        "INDEX_BRANCH": release_metadata.INDEX_BRANCH,
        "RELEASE_TAG": tag,
        "RELEASE_NAME": f"GNATprove {version}",
        "ASSET_NAME": asset,
        "RELEASE_URL": f"https://github.com/{repo}/releases/download/{tag}/{asset}",
        "MANIFEST_PATH": f"index/gn/gnatprove/gnatprove-{version}.toml",
    }


def manifest(origins: dict[str, tuple[str, str]]) -> str:
    """Origins map package architectures to (URL, SHA256)."""
    meta = metadata()
    colibri_arches = [arch for arch in origins if colibri_release(arch)]
    licenses = "GPL-3.0-or-later"
    colibri = ""
    if colibri_arches:
        release = colibri_release(colibri_arches[0])
        licenses += f" AND {release['license']}"
        hosts = ", ".join(release_metadata.PACKAGE_ARCHES[arch] for arch in colibri_arches)
        colibri = f"; COLIBRI {release['version']} on {hosts}"
    text = f'''name = "gnatprove"
version = "{meta['CRATE_VERSION']}"
authors = ["AdaCore"]
description = "Automatic formal verification of SPARK code"
website = "https://github.com/Prunt3D/prunt_alire_index"
maintainers = ["Liam Powell <liam@liampwll.com>"]
maintainers-logins = ["liampwll"]
licenses = "{licenses}"
auto-gpr-with = false
notes = "GNATprove {meta['BASE_VERSION']} with gnat2why from spark2014 commit {meta['SPARK_COMMIT']} and the compiler of Prunt GNAT {meta['GNAT_VERSION']} (GCC commit {meta['GCC_COMMIT']}){colibri}"

[configuration]
disabled = true

[environment]
PATH.prepend = "${{CRATE_ROOT}}/bin"

[available."case(os)"]
linux = true
"..." = false
'''
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


def run(*args, **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def alr_exec(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return run("alr", "-n", f"--chdir={INPUTS}", "exec", "--", *args, **kwargs)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def compiler_prefix() -> Path:
    """The installation prefix of the Prunt compiler in the build crate's environment."""
    gcc = alr_exec("sh", "-c", "command -v gcc", capture_output=True, text=True).stdout.strip()
    return Path(gcc).resolve().parent.parent


def verify_compiler(prefix: Path) -> None:
    """The bundled compiler and gnat2why's front end must come from the gcc submodule's commit."""
    meta = metadata()
    manifest_file = prefix.parent / "alire.toml"
    release = read_toml(manifest_file)
    if release.get("name") != "gnat_native" or release.get("version") != meta["GNAT_VERSION"]:
        raise ValueError(f"{manifest_file} is not gnat_native {meta['GNAT_VERSION']}")
    match = re.fullmatch(r"Built from GCC \S+ commit ([0-9a-f]{40})", release.get("notes", ""))
    if not match or match.group(1) != meta["GCC_COMMIT"]:
        raise ValueError(f"gnat_native {meta['GNAT_VERSION']} was not built from gcc submodule commit "
                         f"{meta['GCC_COMMIT']}: {release.get('notes')!r}")


def build_gnat2why(jobs: int) -> Path:
    run("alr", "-n", f"--chdir={INPUTS}", "build", "--release")
    link = GNAT2WHY_DIR / "gnat_src"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(os.path.relpath(GCC_DIR / "gcc/ada", GNAT2WHY_DIR))
    # AUTOMATED=1 makes gnat2why look for the Ada runtime relative to its own location, at the path the build
    # compiler has it, which is where the archive gets the same compiler's runtime.
    alr_exec("make", "-C", str(GNAT2WHY_DIR), "AUTOMATED=1", "GPRARGS=-XBuild=Production", f"PROCS={jobs}")
    return SPARK_DIR / "install/bin/gnat2why"


def fetch(release: dict, cache: Path) -> Path:
    """Download release["url"], or else release["mirror"], into cache and check it against release["sha256"]."""
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / release.get("file", release["url"].rsplit("/", 1)[1])
    if archive.exists() and sha256(archive) == release["sha256"]:
        return archive
    for url in (release["url"], *([release["mirror"]] if "mirror" in release else [])):
        partial = archive.with_name(archive.name + ".part")
        print(f"+ download {url}", flush=True)
        try:
            with urllib.request.urlopen(url) as response, partial.open("wb") as stream:
                shutil.copyfileobj(response, stream)
        except OSError as error:
            print(f"  failed: {error}", flush=True)
            continue
        if sha256(partial) == release["sha256"]:
            partial.replace(archive)
            return archive
        # A bot-protection page instead of the file, for example.
        print(f"  {url} did not give the file with SHA256 {release['sha256']}", flush=True)
    raise ValueError(f"Could not download {archive.name} with SHA256 {release['sha256']}")


def fetch_base(arch: str, cache: Path) -> Path:
    return fetch(release_config()["base"][arch], cache)


def is_elf(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(4) == b"\x7fELF"


def strip(paths: list[Path]) -> None:
    files = [path for path in paths if is_elf(path)]
    if files:
        run("strip", "--strip-unneeded", *files)


def only_child(directory: Path) -> Path:
    children = [child for child in directory.iterdir()]
    if len(children) != 1 or not children[0].is_dir():
        raise ValueError(f"Expected exactly one directory in {directory}, found {children}")
    return children[0]


def replace_compiler(spark: Path, prefix: Path) -> None:
    """Replace the GCC inside libexec/spark with the Prunt compiler, component by component."""
    old_lib = only_child(only_child(spark / "lib/gcc"))
    old_libexec = spark / "libexec/gcc" / old_lib.parent.name / old_lib.name
    new_lib = only_child(only_child(prefix / "lib/gcc"))
    new_libexec = prefix / "libexec/gcc" / new_lib.parent.name / new_lib.name
    old_triplet, new_triplet = old_lib.parent.name, new_lib.parent.name
    copied = []

    # Driver programs.
    for program in (spark / "bin").iterdir():
        if program.name in COMPILER_PROGRAMS or program.name.startswith(old_triplet + "-"):
            program.unlink()
    for program in (prefix / "bin").iterdir():
        if program.name in COMPILER_PROGRAMS or (program.name.startswith(new_triplet + "-")
                                                 and not program.name.endswith("gfortran")):
            shutil.copy2(program, spark / "bin" / program.name, follow_symlinks=False)
            copied.append(spark / "bin" / program.name)

    # Compiler proper: the programs which the released archive had, from the Prunt compiler.
    programs = sorted(entry.name for entry in old_libexec.iterdir())
    shutil.rmtree(old_libexec.parent)
    target = spark / "libexec/gcc" / new_triplet / new_lib.name
    target.mkdir(parents=True)
    for name in programs:
        source = new_libexec / name
        if source.is_dir():
            shutil.copytree(source, target / name, symlinks=True)
        elif source.exists():
            shutil.copy2(source, target / name, follow_symlinks=False)
            copied.append(target / name)
    if not (target / "gnat1").exists():
        raise ValueError(f"No gnat1 in {new_libexec}")

    # Ada runtime, which gnat2why also reads, and the rest of the compiler's library directory.
    shutil.rmtree(old_lib.parent)
    shutil.copytree(new_lib, spark / "lib/gcc" / new_triplet / new_lib.name, symlinks=True)

    # Shared GCC runtime libraries which both have. The rest of lib64 (GMP) belongs to the provers.
    for library in (spark / "lib64").iterdir():
        replacement = prefix / "lib64" / library.name
        if replacement.exists() and not library.is_dir():
            library.unlink()
            shutil.copy2(replacement, library, follow_symlinks=False)

    strip(copied)


def needed_libraries(path: Path) -> list[str]:
    result = subprocess.run(["readelf", "-d", path], check=True, capture_output=True, text=True).stdout
    return re.findall(r"\(NEEDED\)\s+Shared library: \[([^]]+)\]", result)


def install_colibri(spark: Path, bundle: Path) -> Path:
    """Install a COLIBRI bundle as libexec/spark/colibri, run as bin/colibri, which is on GNATprove's PATH."""
    target = spark / "colibri"
    with tempfile.TemporaryDirectory(dir=spark) as temporary:
        with tarfile.open(bundle) as archive:
            archive.extractall(temporary, filter="tar")
        only_child(Path(temporary)).rename(target)

    # The bundles are for Linux and Windows. Its Linux programs include ECLiPSe's eclipse.exe, so tell them apart
    # by content, not by name.
    for directory in sorted(target.rglob("*_nt"), reverse=True):
        if directory.is_dir():
            shutil.rmtree(directory)
    for path in list(target.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                if stream.read(2) == b"MZ":
                    path.unlink()

    # ECLiPSe's programs were linked against libnsl.so.1, which current glibc no longer provides, but use nothing
    # from it. An empty library with that name, beside them, lets them load where the system has none.
    for directory in sorted({path.parent for path in target.rglob("*")
                             if is_elf(path) and "libnsl.so.1" in needed_libraries(path)}):
        stub = directory / "libnsl.so.1"
        if not stub.exists():
            alr_exec("gcc", "-shared", "-nostdlib", "-Wl,-soname,libnsl.so.1", "-o", str(stub), "-x", "c",
                     "/dev/null")

    # The launcher finds its files through /proc/self/exe, so a symbolic link is enough.
    (spark / "bin/colibri").symlink_to(os.path.relpath(target / "colibri", spark / "bin"))
    return target


def configure_colibri(conf: Path) -> None:
    """Make GNATprove run COLIBRI with the arguments public COLIBRI releases accept."""
    data = json.loads(conf.read_text())
    entries = [prover for prover in data["provers"] if prover["name"] == "colibri"]
    if len(entries) != 1:
        raise ValueError(f"Expected one colibri prover in {conf}")
    entries[0]["args"] = COLIBRI_ARGS
    entries[0]["args_steps"] = COLIBRI_ARGS_STEPS
    conf.write_text(json.dumps(data, indent=2) + "\n")


def assemble(base_archive: Path, prefix: Path, gnat2why: Path, stage: Path,
             colibri_bundle: Path | None = None) -> Path:
    meta = metadata()
    if stage.exists():
        raise ValueError(f"Stage already exists; use a new directory: {stage}")
    stage.mkdir(parents=True)
    with tarfile.open(base_archive) as archive:
        archive.extractall(stage, filter="tar")
    root = only_child(stage)
    package_root = root.with_name(Path(meta["ASSET_NAME"]).name.removesuffix(".tar.gz"))
    root.rename(package_root)
    spark = package_root / "libexec/spark"

    replace_compiler(spark, prefix)
    shutil.copy2(gnat2why, spark / "bin/gnat2why")
    strip([spark / "bin/gnat2why"])

    colibri = colibri_release(meta["PACKAGE_ARCH"])
    if colibri:
        install_colibri(spark, colibri_bundle)
        configure_colibri(package_root / "share/spark/config/gnatprove.conf")
        # COLIBRI is under the LGPL version 2.1, which GCC also ships.
        shutil.copy2(GCC_DIR / "COPYING.LIB", spark / "licenses/lgpl-2.1.txt")

    provenance = package_root / "share/gnatprove_prunt"
    provenance.mkdir(parents=True)
    shutil.copy2(INPUTS / "alire.toml", provenance / "build-inputs.toml")
    shutil.copy2(INPUTS / "release.toml", provenance / "release.toml")
    shutil.copy2(INPUTS / "alire/alire.lock", provenance / "build.lock")
    (provenance / "sources.txt").write_text(
        f"gnatprove {meta['BASE_VERSION']} ({release_config()['base'][meta['PACKAGE_ARCH']]['url']})\n"
        f"spark2014 {meta['SPARK_COMMIT']}\n"
        f"gcc {meta['GCC_COMMIT']}\n"
        + (f"colibri {colibri['version']} ({colibri['url']}; {colibri['license']}, source {colibri['source']})\n"
           if colibri else ""))
    return package_root


def check(package_root: Path) -> None:
    """Reject an archive whose tools are not the ones built here."""
    spark = package_root / "libexec/spark"
    version = subprocess.run([spark / "bin/gcc", "-dumpfullversion"], check=True, capture_output=True,
                             text=True).stdout.strip()
    expected = (GCC_DIR / "gcc/BASE-VER").read_text().strip()
    if version != expected:
        raise ValueError(f"Bundled gcc is {version}, expected {expected}")
    runtimes = list((spark / "lib/gcc").glob("*/*/adainclude"))
    if [runtime.parent.name for runtime in runtimes] != [version]:
        raise ValueError(f"Bundled Ada runtimes {runtimes} do not belong to the bundled gcc {version}")
    colibri = colibri_release(metadata()["PACKAGE_ARCH"])
    if colibri:
        reported = subprocess.run([spark / "bin/colibri", "--version"], check=True, capture_output=True,
                                  text=True).stdout.strip()
        if reported != colibri["version"]:
            raise ValueError(f"Bundled COLIBRI reports version {reported!r}, expected {colibri['version']}")
        with tempfile.TemporaryDirectory() as temporary:
            problem = Path(temporary) / "check.smt2"
            problem.write_text(COLIBRI_CHECK)
            arguments = [argument.replace("%m", "1000").replace("%f", str(problem)) for argument in COLIBRI_ARGS]
            answer = subprocess.run([spark / "bin/colibri", *arguments], capture_output=True, text=True)
        if answer.stdout.split()[:1] != ["unsat"]:
            raise ValueError(f"Bundled COLIBRI did not solve its check problem:\n{answer.stdout}{answer.stderr}")
    run(package_root / "bin/gnatprove", "--version")


def package(package_root: Path, dist: Path) -> Path:
    dist.mkdir(parents=True, exist_ok=True)
    archive = dist / (package_root.name + ".tar.gz")
    # One enclosing directory is required by Alire's archive extraction.
    run("tar", "--sort=name", "--mtime=UTC 1970-01-01", "--owner=0", "--group=0",
        "--numeric-owner", "-czf", archive, "-C", package_root.parent, package_root.name)
    archive.with_name(archive.name + ".sha256").write_text(sha256(archive) + "\n")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    meta_parser = sub.add_parser("metadata")
    meta_parser.add_argument("--repo-slug")
    meta_parser.add_argument("--format", choices=("json", "github-env"), default="json")
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--stage", type=Path, default=ROOT / "stage/gnatprove")
    build_parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    build_parser.add_argument("--cache", type=Path, default=ROOT / "build/gnatprove-base")
    build_parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    build_parser.add_argument("--reuse-gnat2why", action="store_true",
                              help="Package the gnat2why already built in spark2014/install (local development only)")
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
        meta = metadata()
        if args.reuse_gnat2why:
            gnat2why = SPARK_DIR / "install/bin/gnat2why"
        else:
            gnat2why = build_gnat2why(args.jobs)
        prefix = compiler_prefix()
        verify_compiler(prefix)
        base = fetch_base(meta["PACKAGE_ARCH"], args.cache.resolve())
        colibri = colibri_release(meta["PACKAGE_ARCH"])
        colibri_bundle = fetch(colibri, args.cache.resolve()) if colibri else None
        package_root = assemble(base, prefix, gnat2why, args.stage.resolve(), colibri_bundle)
        check(package_root)
        print(package(package_root, args.dist.resolve()))
    else:
        origins = {}
        for arch in release_metadata.PACKAGE_ARCHES:
            meta = metadata(args.repo_slug, arch)
            archive = args.dist / meta["ASSET_NAME"]
            recorded = archive.with_name(archive.name + ".sha256").read_text().strip()
            if recorded != sha256(archive):
                raise ValueError(f"Checksum mismatch: {archive}")
            origins[arch] = (meta["RELEASE_URL"], recorded)
        output = ROOT / metadata()["MANIFEST_PATH"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(manifest(origins))
        print(output)


if __name__ == "__main__":
    main()
