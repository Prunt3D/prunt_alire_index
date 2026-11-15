#!/usr/bin/env python3
"""Install the GNATprove archive through Alire in a fresh workspace and prove code only Prunt's front end accepts."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import gnatprove_release as release


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    archive = args.archive.resolve()
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    meta = release.metadata()
    with tempfile.TemporaryDirectory(prefix="gnatprove-smoke-") as temporary:
        root = Path(temporary)
        index = root / "index"
        manifest = index / meta["MANIFEST_PATH"]
        manifest.parent.mkdir(parents=True)
        (index / "index/index.toml").write_text('version = "1.4.0"\n')
        manifest.write_text(release.manifest({meta["PACKAGE_ARCH"]: (archive.as_uri(), digest)}))
        consumer = root / "consumer"
        shutil.copytree(release.ROOT / "tests/gnatprove", consumer)
        (consumer / "alire.toml").write_text(f'''name = "smoke"
description = "Test GNATprove distribution"
version = "0.0.0"
licenses = "MIT"
project-files = ["smoke.gpr"]
[[depends-on]]
gnatprove = "={meta['CRATE_VERSION']}"
gnat_native = "={meta['GNAT_VERSION']}"
''')

        name = f"prunt_gnatprove_smoke_{os.getpid()}"

        def alr(*arguments, **kwargs):
            return release.run("alr", "-n", *arguments, cwd=consumer, **kwargs)

        alr("index", f"--add={index}", f"--name={name}", "--before=community")
        try:
            alr("update")
            lock = release.read_toml(consumer / "alire/alire.lock")
            states = {state["crate"]: state for state in lock["solution"]["state"]}
            if states["gnatprove"]["release"]["version"] != meta["CRATE_VERSION"]:
                raise ValueError(f"Resolved another GNATprove: {states['gnatprove']}")
            # Every check must be proved, and nothing may be left unanalysed because of a front-end error.
            result = alr("exec", "--", "gnatprove", "-P", "smoke.gpr", "--level=1", "--checks-as-errors=on",
                         "--report=all", "-j0", capture_output=True, text=True)
            output = result.stdout + result.stderr
            print(output)
            if "Success: all checks proved" not in output or "postcondition proved" not in output:
                raise ValueError("GNATprove did not prove Dims.Travel")
            representation = consumer / "obj/gnatprove/data_representation_generation.log"
            if representation.exists() and "error" in representation.read_text():
                raise ValueError(f"Bundled compiler rejected the sources:\n{representation.read_text()}")
            print("GNATprove archive resolved through Alire and proved the dimensioned sources.")
        except subprocess.CalledProcessError as error:
            print(error.stdout, error.stderr, sep="\n")
            raise
        finally:
            alr("index", f"--del={name}")


if __name__ == "__main__":
    main()
