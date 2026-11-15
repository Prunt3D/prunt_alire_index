import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import libadalang_release as libadalang
import gnatprove_release as gnatprove
import release_metadata as gcc
import publish_release as publisher


class ReleaseTests(unittest.TestCase):
    def test_existing_draft_is_found_when_tag_endpoint_returns_404(self):
        draft = {"tag_name": "tag", "draft": True, "assets": []}
        response = subprocess.CompletedProcess([], 1, "", "Not Found (HTTP 404)")
        with patch.object(publisher.subprocess, "run", return_value=response), \
             patch.object(publisher, "gh", return_value=json.dumps([[], [draft]])):
            self.assertEqual(publisher.get_release("owner/repo", "tag"), draft)

    def test_api_failure_does_not_start_a_new_release(self):
        response = subprocess.CompletedProcess([], 1, "", "Bad credentials (HTTP 401)")
        with patch.object(publisher.subprocess, "run", return_value=response), \
             patch.object(publisher, "gh") as gh:
            with self.assertRaisesRegex(RuntimeError, "Bad credentials"):
                publisher.get_release("owner/repo", "tag")
            gh.assert_not_called()

    def test_release_publication_can_resume_without_overwriting_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary)
            archive = dist / "library.tar.gz"
            archive.write_bytes(b"archive contents")
            checksum = dist / "library.tar.gz.sha256"
            checksum.write_text(publisher.digest(archive) + "\n")

            def download_existing(*args):
                if args[:2] == ("release", "download"):
                    shutil.copy2(archive, Path(args[args.index("--dir") + 1]) / archive.name)
                return ""

            with patch.object(publisher, "get_release", return_value={
                "assets": [{"name": archive.name}], "draft": True,
            }), patch.object(publisher, "gh", side_effect=download_existing) as gh:
                publisher.publish("owner/repo", "tag", "title", "notes", "commit", dist)
            calls = [call.args for call in gh.call_args_list]
            uploads = [call for call in calls if call[:2] == ("release", "upload")]
            self.assertEqual(len(uploads), 1)
            self.assertIn(str(checksum), uploads[0])
            self.assertNotIn(str(archive), uploads[0])
            self.assertEqual(calls[-1][:2], ("release", "edit"))
            self.assertIn("--draft=false", calls[-1])

    def test_release_publication_refuses_changed_existing_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary)
            archive = dist / "library.tar.gz"
            archive.write_bytes(b"new build")
            (dist / "library.tar.gz.sha256").write_text(publisher.digest(archive) + "\n")

            def download_different(*args):
                destination = Path(args[args.index("--dir") + 1]) / archive.name
                destination.write_bytes(b"previous build")
                return ""

            with patch.object(publisher, "get_release", return_value={
                "assets": [{"name": archive.name}], "draft": False,
            }), patch.object(publisher, "gh", side_effect=download_different) as gh:
                with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
                    publisher.publish("owner/repo", "tag", "title", "notes", "commit", dist)
                self.assertEqual(gh.call_count, 1)

    def test_binary_manifest_allows_any_compiler_and_replaces_entire_library_closure(self):
        data = tomllib.loads(libadalang.manifest({
            "x86_64": ("https://example.com/x86.tar.gz", "a" * 64),
            "aarch64": ("https://example.com/arm.tar.gz", "b" * 64),
        }))
        self.assertEqual(data["depends-on"], [{"gnat": "*", "libgmp": "*"}])
        self.assertEqual(set(data["forbids"][0]), set(libadalang.LIBRARIES))
        self.assertIn("vss_text=26.2.0", data["provides"])
        self.assertTrue(data["configuration"]["disabled"])
        self.assertNotIn("actions", data)
        origins = data["origin"]["case(os)"]["linux"]["case(host-arch)"]
        self.assertEqual(set(origins), {"x86-64", "aarch64"})
        self.assertTrue(all(origin["binary"] for origin in origins.values()))

    def test_placeholder_checksums_cannot_be_published(self):
        for digest in ("0" * 64, "", "not-a-hash"):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                libadalang.manifest({"x86_64": ("https://example.com/test.tar.gz", digest)})

    def test_libadalang_metadata_does_not_need_gcc_checkout_or_release(self):
        pass

    def test_gcc_metadata_without_initialized_submodule(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(gcc, "GCC_DIR", Path(directory) / "gcc"):
                meta = gcc.metadata("owner/repo", "x86_64")
            expected = subprocess.check_output(
                ["git", "rev-parse", "HEAD:gcc"], cwd=ROOT, text=True).strip()
            self.assertEqual(meta["GCC_COMMIT"], expected)

    def test_only_gnat_release_inputs_trigger_automatic_gcc_builds(self):
        workflow = yaml.load((ROOT / ".github/workflows/build-gcc.yml").read_text(),
                             Loader=yaml.BaseLoader)
        trigger = workflow["on"]["push"]
        self.assertEqual(trigger["branches"], ["master"])
        for path, expected in (
            ("gcc", True), ("release.toml", True),
            ("libadalang/release.toml", False), ("libadalang/alire.toml", False),
            ("scripts/libadalang_release.py", False), ("scripts/generate_index.py", False),
            ("scripts/export_index_branch.sh", False), ("README.md", False),
            (".github/workflows/build-gcc.yml", False),
            (".github/workflows/build-libadalang.yml", False),
            ("index/li/libadalang_prebuilt/libadalang_prebuilt-26.0.1001.toml", False),
        ):
            with self.subTest(path=path):
                self.assertEqual(any(fnmatch.fnmatchcase(path, pattern)
                                     for pattern in trigger["paths"]), expected)

    def test_gnatprove_manifest_stands_alone_and_names_its_sources(self):
        data = tomllib.loads(gnatprove.manifest({
            "x86_64": ("https://example.com/x86.tar.gz", "a" * 64),
            "aarch64": ("https://example.com/arm.tar.gz", "b" * 64),
        }))
        meta = gnatprove.metadata("owner/repo", "x86_64")
        self.assertEqual(data["name"], "gnatprove")
        self.assertEqual(data["version"], meta["CRATE_VERSION"])
        # It bundles its compiler, so it must not constrain the consumer's toolchain.
        self.assertNotIn("depends-on", data)
        self.assertNotIn("provides", data)
        self.assertTrue(data["configuration"]["disabled"])
        self.assertEqual(data["environment"]["PATH"]["prepend"], "${CRATE_ROOT}/bin")
        for commit in (meta["GCC_COMMIT"], meta["SPARK_COMMIT"]):
            self.assertIn(commit, data["notes"])
        origins = data["origin"]["case(os)"]["linux"]["case(host-arch)"]
        self.assertEqual(set(origins), {"x86-64", "aarch64"})
        self.assertTrue(all(origin["binary"] for origin in origins.values()))

    def test_gnatprove_placeholder_checksums_cannot_be_published(self):
        for digest in ("0" * 64, "", "not-a-hash"):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                gnatprove.manifest({"x86_64": ("https://example.com/test.tar.gz", digest)})

    def test_gnatprove_metadata_without_initialized_submodules(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(gnatprove, "GCC_DIR", Path(directory) / "gcc"), \
                 patch.object(gnatprove, "SPARK_DIR", Path(directory) / "spark2014"):
                meta = gnatprove.metadata("owner/repo", "x86_64")
        for name, key in (("gcc", "GCC_COMMIT"), ("spark2014", "SPARK_COMMIT")):
            expected = subprocess.check_output(
                ["git", "rev-parse", f"HEAD:{name}"], cwd=ROOT, text=True).strip()
            self.assertEqual(meta[key], expected)

    def test_gnatprove_release_names(self):
        meta = gnatprove.metadata("owner/repo", "aarch64")
        self.assertEqual(meta["ASSET_NAME"], f"gnatprove-aarch64-linux-{meta['CRATE_VERSION']}.tar.gz")
        self.assertEqual(meta["MANIFEST_PATH"], f"index/gn/gnatprove/gnatprove-{meta['CRATE_VERSION']}.toml")
        self.assertTrue(meta["RELEASE_URL"].startswith(
            f"https://github.com/owner/repo/releases/download/gnatprove-{meta['CRATE_VERSION']}/"))

    def test_gnatprove_version_follows_the_gcc_it_is_built_on(self):
        meta = gnatprove.metadata("owner/repo", "x86_64")
        self.assertEqual(meta["CRATE_VERSION"].split(".")[:2], meta["GNAT_VERSION"].split(".")[:2])
        with patch.object(gnatprove, "release_config",
                          return_value={**gnatprove.release_config(), "crate_version": "16.1.1001"}):
            with self.assertRaisesRegex(ValueError, "not in the series"):
                gnatprove.metadata("owner/repo", "x86_64")

    def test_gnatprove_base_release_is_pinned_for_every_architecture(self):
        base = gnatprove.release_config()["base"]
        for arch in gcc.PACKAGE_ARCHES:
            with self.subTest(arch=arch):
                self.assertRegex(base[arch]["sha256"], r"^[0-9a-f]{64}$")
                self.assertIn(base["version"], base[arch]["url"])
                self.assertIn(f"{arch}-linux", base[arch]["url"])

    def test_gnatprove_requires_the_compiler_released_from_the_gcc_submodule(self):
        meta = gnatprove.metadata()
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "local"
            prefix.mkdir()
            for commit, accepted in ((meta["GCC_COMMIT"], True), ("1" * 40, False)):
                (prefix.parent / "alire.toml").write_text(
                    f'name = "gnat_native"\nversion = "{meta["GNAT_VERSION"]}"\n'
                    f'notes = "Built from GCC {meta["GNAT_VERSION"]} commit {commit}"\n')
                with self.subTest(accepted=accepted):
                    if accepted:
                        gnatprove.verify_compiler(prefix)
                    else:
                        with self.assertRaisesRegex(ValueError, "not built from"):
                            gnatprove.verify_compiler(prefix)

    def test_gnatprove_compiler_is_replaced_component_by_component(self):
        def tree(root, files):
            for path in files:
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(f"{root.name}:{path}")

        with tempfile.TemporaryDirectory() as directory:
            spark, prefix = Path(directory) / "spark", Path(directory) / "prefix"
            tree(spark, [
                "bin/gcc", "bin/gnatls", "bin/x86_64-pc-linux-gnu-gcc-16.1.0", "bin/gnat2why", "bin/cvc5",
                "lib/gcc/x86_64-pc-linux-gnu/16.1.0/adainclude/system.ads",
                "libexec/gcc/x86_64-pc-linux-gnu/16.1.0/gnat1", "libexec/gcc/x86_64-pc-linux-gnu/16.1.0/cc1",
                "libexec/gcc/x86_64-pc-linux-gnu/16.1.0/plugin/include/x.h",
                "lib64/libgcc_s.so.1", "lib64/libgmp.so.10",
            ])
            tree(prefix, [
                "bin/gcc", "bin/gnatls", "bin/gfortran", "bin/gnatmake", "bin/x86_64-pc-linux-gnu-gcc-16.2.0",
                "bin/x86_64-pc-linux-gnu-gfortran",
                "lib/gcc/x86_64-pc-linux-gnu/16.2.0/adainclude/system.ads",
                "libexec/gcc/x86_64-pc-linux-gnu/16.2.0/gnat1", "libexec/gcc/x86_64-pc-linux-gnu/16.2.0/cc1",
                "libexec/gcc/x86_64-pc-linux-gnu/16.2.0/f951",
                "libexec/gcc/x86_64-pc-linux-gnu/16.2.0/plugin/include/x.h",
                "lib64/libgcc_s.so.1", "lib64/libgfortran.so.5",
            ])
            gnatprove.replace_compiler(spark, prefix)
            files = {str(path.relative_to(spark)): path.read_text()
                     for path in spark.rglob("*") if path.is_file()}
            self.assertEqual(files, {
                "bin/gcc": "prefix:bin/gcc",
                "bin/gnatls": "prefix:bin/gnatls",
                "bin/x86_64-pc-linux-gnu-gcc-16.2.0": "prefix:bin/x86_64-pc-linux-gnu-gcc-16.2.0",
                "bin/gnat2why": "spark:bin/gnat2why",
                "bin/cvc5": "spark:bin/cvc5",
                "lib/gcc/x86_64-pc-linux-gnu/16.2.0/adainclude/system.ads":
                    "prefix:lib/gcc/x86_64-pc-linux-gnu/16.2.0/adainclude/system.ads",
                "libexec/gcc/x86_64-pc-linux-gnu/16.2.0/gnat1": "prefix:libexec/gcc/x86_64-pc-linux-gnu/16.2.0/gnat1",
                "libexec/gcc/x86_64-pc-linux-gnu/16.2.0/cc1": "prefix:libexec/gcc/x86_64-pc-linux-gnu/16.2.0/cc1",
                "libexec/gcc/x86_64-pc-linux-gnu/16.2.0/plugin/include/x.h":
                    "prefix:libexec/gcc/x86_64-pc-linux-gnu/16.2.0/plugin/include/x.h",
                "lib64/libgcc_s.so.1": "prefix:lib64/libgcc_s.so.1",
                "lib64/libgmp.so.10": "spark:lib64/libgmp.so.10",
            })

    def test_gnatprove_waits_for_the_compiler_it_bundles_to_be_published(self):
        meta = gnatprove.metadata()

        def published(returncode, commit=None):
            manifest = (f'name = "gnat_native"\nversion = "{meta["GNAT_VERSION"]}"\n'
                        f'notes = "Built from GCC {meta["GNAT_VERSION"]} commit {commit}"\n')
            return subprocess.CompletedProcess([], returncode, manifest if returncode == 0 else "", "")

        for answer, message in ((published(128), "not in the published index"),
                                (published(0, "1" * 40), "built from GCC commit"),
                                (published(0, meta["GCC_COMMIT"]), None)):
            with self.subTest(message=message), patch.object(gnatprove, "metadata", return_value=meta), \
                 patch.object(gnatprove.subprocess, "run", return_value=answer) as run:
                if message:
                    with self.assertRaisesRegex(SystemExit, message):
                        gnatprove.check_compiler_published("FETCH_HEAD")
                else:
                    gnatprove.check_compiler_published("FETCH_HEAD")
                self.assertEqual(run.call_args.args[0], [
                    "git", "show",
                    f"FETCH_HEAD:index/gn/gnat_native/gnat_native-{meta['GNAT_VERSION']}.toml"])

    def test_colibri_is_pinned_only_where_cea_publishes_it(self):
        x86 = gnatprove.colibri_release("x86_64")
        self.assertRegex(x86["sha256"], r"^[0-9a-f]{64}$")
        self.assertIn(x86["version"], x86["url"])
        self.assertTrue(x86["mirror"].startswith("https://zenodo.org/"))
        self.assertTrue(x86["license"].startswith("LGPL-2.1"))
        self.assertIsNone(gnatprove.colibri_release("aarch64"))

    def test_gnatprove_manifest_declares_bundled_colibri(self):
        data = tomllib.loads(gnatprove.manifest({
            "x86_64": ("https://example.com/x86.tar.gz", "a" * 64),
            "aarch64": ("https://example.com/arm.tar.gz", "b" * 64),
        }))
        colibri = gnatprove.colibri_release("x86_64")
        self.assertEqual(data["licenses"], f"GPL-3.0-or-later AND {colibri['license']}")
        self.assertIn(f"COLIBRI {colibri['version']} on x86-64", data["notes"])
        self.assertNotIn("aarch64", data["notes"].split("COLIBRI")[1])

    def test_fetch_falls_back_to_the_mirror_and_checks_the_file(self):
        good, bad = b"bundle", b"<html>bot check</html>"
        digest = hashlib.sha256(good).hexdigest()
        release = {"url": "https://primary.example/bundle", "mirror": "https://mirror.example/bundle",
                   "file": "bundle.tbz", "sha256": digest}

        def opener(responses):
            def urlopen(url):
                answer = responses[url]
                if isinstance(answer, Exception):
                    raise answer
                return io.BytesIO(answer)
            return urlopen

        for primary in (OSError("refused"), bad):
            with self.subTest(primary=primary), tempfile.TemporaryDirectory() as directory, \
                 patch.object(gnatprove.urllib.request, "urlopen",
                              opener({release["url"]: primary, release["mirror"]: good})):
                self.assertEqual(gnatprove.fetch(release, Path(directory)).read_bytes(), good)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(gnatprove.urllib.request, "urlopen",
                          opener({release["url"]: bad, release["mirror"]: bad})):
            with self.assertRaisesRegex(ValueError, "Could not download"):
                gnatprove.fetch(release, Path(directory))

    def test_colibri_gets_the_arguments_of_public_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            conf = Path(directory) / "gnatprove.conf"
            other = {"name": "altergo", "args": ["%f"], "args_steps": ["--steps-bound", "%S"]}
            conf.write_text(json.dumps({"magic": 14, "provers": [
                other, {"name": "colibri", "args": ["--memlimit", "%m", "%f"],
                        "args_steps": ["--steplimit", "%S"], "version": "2137"}]}))
            gnatprove.configure_colibri(conf)
            data = json.loads(conf.read_text())
        self.assertEqual(data["magic"], 14)
        self.assertEqual(data["provers"][0], other)
        self.assertEqual(data["provers"][1], {"name": "colibri", "args": gnatprove.COLIBRI_ARGS,
                                              "args_steps": gnatprove.COLIBRI_ARGS_STEPS, "version": "2137"})
        self.assertIn("%f", gnatprove.COLIBRI_ARGS)

    def test_colibri_bundle_loses_only_its_windows_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_root = root / "bundle/colibri-1.0-v7"
            files = {
                "colibri": b"launcher", "colibri.exe": b"launcher", "version": b"1.0",
                "COLIBRI/lib/v7/x86_64_linux/float_util.so": b"linux library",
                "COLIBRI/lib/v7/x86_64_nt/float_util.dll": b"MZ windows library",
                "ECLIPSE/lib/x86_64_linux/eclipse.exe": b"linux engine",
                "ECLIPSE/lib/x86_64_nt/eclipse.exe": b"MZ windows engine",
                "COLIBRI/filter_smtlib_file.exe": b"MZ windows tool",
            }
            for name, content in files.items():
                (bundle_root / name).parent.mkdir(parents=True, exist_ok=True)
                (bundle_root / name).write_bytes(content)
            bundle = root / "bundle.tbz"
            with tarfile.open(bundle, "w:bz2") as archive:
                archive.add(bundle_root, arcname=bundle_root.name)
            spark = root / "spark"
            (spark / "bin").mkdir(parents=True)
            target = gnatprove.install_colibri(spark, bundle)
            kept = {str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()}
            self.assertEqual(kept, {"colibri", "colibri.exe", "version",
                                    "COLIBRI/lib/v7/x86_64_linux/float_util.so",
                                    "ECLIPSE/lib/x86_64_linux/eclipse.exe"})
            self.assertFalse(list(target.rglob("*_nt")))
            launcher = spark / "bin/colibri"
            self.assertEqual(os.readlink(launcher), "../colibri/colibri")
            self.assertEqual(launcher.read_bytes(), b"launcher")

    def test_only_gnatprove_inputs_trigger_gnatprove_builds(self):
        workflow = yaml.load((ROOT / ".github/workflows/build-gnatprove.yml").read_text(),
                             Loader=yaml.BaseLoader)
        trigger = workflow["on"]["push"]
        self.assertEqual(trigger["branches"], ["master"])
        for path, expected in (
            ("spark2014", True), ("gnatprove/release.toml", True), ("gnatprove/alire.toml", True),
            ("scripts/gnatprove_release.py", True), ("tests/gnatprove/src/dims.ads", True),
            ("gcc", False), ("release.toml", False), ("libadalang/release.toml", False),
            ("index/gn/gnatprove/gnatprove-16.2.1001.toml", False),
        ):
            with self.subTest(path=path):
                self.assertEqual(any(fnmatch.fnmatchcase(path, pattern)
                                     for pattern in trigger["paths"]), expected)

    def test_publish_jobs_serialize_index_updates(self):
        groups = []
        for name in ("build-gcc.yml", "build-libadalang.yml", "build-gnatprove.yml", "publish-index.yml"):
            workflow = yaml.load((ROOT / ".github/workflows" / name).read_text(),
                                 Loader=yaml.BaseLoader)
            concurrency = workflow["jobs"]["publish"]["concurrency"]
            self.assertEqual(concurrency["cancel-in-progress"], "false")
            groups.append(concurrency["group"])
        self.assertEqual(len(set(groups)), 1)

    def test_index_export_preserves_both_packages_and_older_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "source"
            remote = root / "remote.git"

            def git(*args, cwd=repository):
                return subprocess.check_output(["git", *args], cwd=cwd, text=True,
                                               stderr=subprocess.DEVNULL).strip()

            git("init", "--bare", str(remote), cwd=root)
            git("init", "--initial-branch=master", str(repository), cwd=root)
            git("config", "user.name", "Release test")
            git("config", "user.email", "test@example.com")
            git("remote", "add", "origin", str(remote))
            (repository / "scripts").mkdir()
            shutil.copy2(ROOT / "scripts/export_index_branch.sh", repository / "scripts")
            (repository / "index").mkdir()
            (repository / "index/index.toml").write_text('version = "1.4.0"\n')
            old = Path("index/gn/gnat_native/gnat_native-1.0.0.toml")
            new = Path("index/li/libadalang_prebuilt/libadalang_prebuilt-26.0.1001.toml")
            latest = Path("index/gn/gnat_native/gnat_native-2.0.0.toml")
            (repository / old).parent.mkdir(parents=True)
            (repository / old).write_text("old compiler\n")
            git("add", ".")
            git("commit", "-m", "Initial index")
            git("push", "origin", "HEAD:alire-index")
            (repository / old).unlink()
            for path, content in ((new, "static bundle\n"), (latest, "new compiler\n")):
                (repository / path).parent.mkdir(parents=True, exist_ok=True)
                (repository / path).write_text(content)
                subprocess.run(["bash", "scripts/export_index_branch.sh", "--push"],
                               cwd=repository, check=True, capture_output=True)
                (repository / path).unlink()
            for path, content in ((old, "old compiler"), (new, "static bundle"),
                                  (latest, "new compiler")):
                self.assertEqual(git("show", f"alire-index:{path}", cwd=remote), content)


if __name__ == "__main__":
    unittest.main()
