#!/usr/bin/env python3
"""Check the installed Ada runtime's LTO and native-code paths."""

import argparse
import os
from pathlib import Path
import re
import subprocess


SOURCES = Path(__file__).resolve().parent.parent / "tests/gcc"


def run(*args, cwd=None, env=None):
    return subprocess.check_output(args, cwd=cwd, env=env, text=True)


def check_fat_object(archive, member, directory, gcc_ar, env):
    obj = directory / member
    obj.write_bytes(subprocess.check_output([gcc_ar, "p", archive, member], env=env))
    sections = {}
    for line in run("objdump", "-h", str(obj), env=env).splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0].isdigit():
            sections[fields[1]] = int(fields[2], 16)
    if not any(name.startswith(".gnu.lto_") for name in sections):
        raise RuntimeError(f"{archive}({member}) has no LTO bytecode")
    if not any(name.startswith(".text") and size for name, size in sections.items()):
        raise RuntimeError(f"{archive}({member}) has no native code")


def check_inlining(directory):
    dumps = list(directory.glob("*.ltrans*.optimized"))
    for dump in dumps:
        for function in dump.read_text().split(";; Function ")[1:]:
            header, _, body = function.partition("\n")
            if "(_ada_lto_runtime," not in header:
                continue
            if not re.search(r"\b(?:__builtin_)?sqrt\s*\(", body):
                raise RuntimeError("LTO did not expose the square-root intrinsic in the caller")
            if re.search(r"long_elementary_functions[._]+(?:Oexpon|sqrt|\"\*\*\")"
                         r"(?:\.[\w.]+)?\s*\(", body):
                raise RuntimeError("An elementary-function wrapper call survived LTO")
            return
    raise RuntimeError("Cannot find Lto_Runtime in the final LTO optimized dumps")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--test-dir", type=Path, required=True)
    args = parser.parse_args()
    prefix = args.prefix.resolve()
    test_dir = args.test_dir.resolve()
    test_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    for name in ("ADA_INCLUDE_PATH", "ADA_OBJECT_PATH", "ADA_OBJECTS_PATH"):
        env.pop(name, None)
    env["PATH"] = f"{prefix / 'bin'}:{env.get('PATH', '')}"
    for name in ("LIBRARY_PATH", "LD_LIBRARY_PATH", "LD_RUN_PATH"):
        env[name] = f"{prefix / 'lib64'}:{prefix / 'lib'}:{env.get(name, '')}"
    gcc = str(prefix / "bin/gcc")
    gnatmake = str(prefix / "bin/gnatmake")
    gcc_ar = str(prefix / "bin/gcc-ar")
    runtime = Path(run(gcc, "-print-file-name=adalib", env=env).strip()).resolve()
    if not runtime.is_relative_to(prefix) or not runtime.is_dir():
        raise RuntimeError(f"Compiler selected a runtime outside the staged prefix: {runtime}")

    for library, member in (("libgnat.a", "a-nlelfu.o"), ("libgnat.a", "init.o"),
                            ("libgnarl.a", "s-tassta.o")):
        check_fat_object(str(runtime / library), member, test_dir, gcc_ar, env)

    for mode, flag, check_flags in (("native", "-fno-lto", []),
                                    ("lto-checked", "-flto", []),
                                    ("lto-release", "-flto", ["-gnatp"])):
        directory = test_dir / mode
        directory.mkdir()
        link_flags = ["-fdump-tree-optimized"] if mode == "lto-release" else []
        for name in ("lto_runtime", "runtime_checks"):
            run(gnatmake, "-q", str(SOURCES / f"{name}.adb"), "-o", name,
                "-cargs", "-O3", flag, *check_flags,
                "-largs", "-O3", flag, *link_flags, cwd=directory, env=env)
            executable = directory / name
            dependencies = run("readelf", "-d", str(executable), env=env)
            if re.search(r"NEEDED.*libgna(?:t|rl)", dependencies):
                raise RuntimeError(f"{mode} test unexpectedly linked a shared Ada runtime")
            run(str(executable), env=env)
        if mode == "lto-release":
            check_inlining(directory)
        print(f"Ada runtime {mode} smoke test passed")


if __name__ == "__main__":
    main()
