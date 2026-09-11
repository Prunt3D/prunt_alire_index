import fnmatch
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import libadalang_release as libadalang
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

    def test_binary_manifest_pins_compiler_and_replaces_entire_library_closure(self):
        data = tomllib.loads(libadalang.manifest({
            "x86_64": ("https://example.com/x86.tar.gz", "a" * 64),
            "aarch64": ("https://example.com/arm.tar.gz", "b" * 64),
        }))
        self.assertEqual(data["depends-on"][0]["gnat_native"], "=16.2.1001")
        self.assertEqual(data["depends-on"][0]["gnat"], "=16.2.1001")
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
        with patch.object(gcc, "metadata", side_effect=AssertionError("Must not inspect GCC")):
            self.assertEqual(libadalang.metadata("owner/repo", "aarch64")["GNAT_VERSION"],
                             "16.2.1001")

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

    def test_publish_jobs_serialize_index_updates(self):
        groups = []
        for name in ("build-gcc.yml", "build-libadalang.yml", "publish-index.yml"):
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
