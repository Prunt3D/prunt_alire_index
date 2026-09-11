# Prunt Alire Index

This repository builds GCC and a precompiled static Libadalang bundle for Linux
x86-64 and ARM64. Build tooling lives on `master`; the default `alire-index`
branch contains only published manifests. Clone `master` for development:

```bash
git clone --branch master https://github.com/Prunt3D/prunt_alire_index.git
```

Only GCC builders need to initialize the `gcc/` submodule.

## What triggers work

| Change pushed to `master` | Work performed |
| --- | --- |
| `gcc` gitlink or root `release.toml` | Build and publish GCC for both architectures |
| `libadalang/**`, its packaging script, or its build workflow | Build and publish the static library bundle using an existing GNAT release |
| `index/**` | Validate and publish index changes, without building packages |
| Other scripts, tests, or workflows | Lightweight validation only |
| Documentation | No package build |

Changes to GCC build scripts or compiler packaging require a version bump in
root `release.toml` to produce a new compiler release. Editing a shared script
does not rebuild GNAT. Libadalang's compiler version is pinned independently;
releasing a new GCC does not automatically rebuild Libadalang. To adopt it,
update the compiler dependencies in `libadalang/alire.toml` and increment
`libadalang/release.toml` in the same commit, after the GNAT release is published.

All publishers serialize updates to `alire-index` and preserve existing package
versions. Releases are not overwritten: use a new version for changed inputs.
Both build workflows refuse an existing release before starting compilation.
The first push of this setup builds Libadalang, without rebuilding the already
released GCC version.

If publication fails, re-run only the failed publish job within the seven-day
artifact retention period. The publisher resumes missing uploads, verifies any
existing assets byte-for-byte, and retries the index update without rebuilding.
Releases remain drafts until all archives and checksums have been uploaded.

`workflow_dispatch` is retained for build workflows, but GitHub only enables it
when the workflow also exists on the default branch. With this repository's
index-only default branch, use the normal version-update push to `master`.

## Release flow

Run the release preparation script from a clean checkout:

```bash
./scripts/prepare_release.sh
```

The script fetches the latest `releases/gcc-16` commit from the GCC fork,
checks out that exact revision even when the release branch was rebased,
advances the Alire crate to the fetched GCC major/minor series (resetting its
package revision to `1001`) or increments the revision within the same series,
validates the release metadata, and creates the release commit. It does not
push. Use `--dry-run` to report the proposed GCC and version changes without
modifying the checkout.

Review and push the resulting commit. GitHub Actions then builds GCC out of
tree, smoke-tests it, uploads release assets, regenerates the index manifest,
and publishes the updated manifest to `alire-index`.

## Static Libadalang

The `libadalang_prebuilt` crate contains Libadalang 26.0.0, its complete Ada
library dependency closure, installed GPR projects, ALI metadata, interface and
generic-body sources, and upstream license notices. It includes Prunt's pinned
LibGPR, GPR2 and Langkit Support forks, and VSS Text 26.2.0. The compiler is
downloaded from the existing index; GCC is neither checked out nor compiled by
this workflow. The initial bundle requires Prunt GNAT 16.2.1001, matching the
current Prunt `config_codegen` build.

All bundled Ada libraries are static and marked externally built. System GMP
remains a dependency (`libgmp-dev` on Ubuntu); this is not a fully static libc/GMP
distribution. CI builds on Ubuntu 24.04, so supported hosts need a compatible
glibc baseline. Separate archives and SHA256 checksums are produced for x86-64
and aarch64. A new index manifest is generated only from the actual archives
after both architecture builds and smoke tests pass; no placeholder checksums
are committed.

Each architecture's smoke test installs its archive through a temporary Alire
index with the original source-build directories moved out of the way,
resolves the library and VSS dependencies through the bundle, builds and
runs an Ada parser client directly, checks for unwanted Ada shared-library
dependencies, and verifies that library/source/ALI files were not rebuilt or
modified. The package uses `provides` and `forbids` for its Ada dependencies to
prevent mixing bundled libraries with separately built copies.

After GitHub Actions publishes the initial release, replace Prunt's
`config_codegen/alire.toml` with `examples/config_codegen/alire.toml` from this
repository and run:

```bash
alr index --update-all
alr --chdir=config_codegen update
alr --chdir=config_codegen build --development
```

The replacement removes the old source dependency pins as well as the explicit
VSS source dependency. Existing `with "libadalang.gpr"` statements continue to
work, and `prebuild` can still run the generator directly. Do not retain the old
Libadalang/Langkit/GPR/VSS pins alongside the bundle. Prunt's main crate retains
its own source dependencies; the binary package belongs to the separate
`config_codegen` workspace.

For a local build (Python 3.11+, Alire 2.1, system build tools and GMP development
files, with this index registered):

```bash
python3 scripts/libadalang_release.py build
python3 scripts/smoke_test_libadalang.py dist/libadalang-static-$(uname -m)-linux-*.tar.gz
```

The stage directory must be new for each packaging run. To exercise packaging
against an existing matching build during development, use
`build --reuse-build /path/to/prunt/config_codegen --stage /new/stage/path`.
This bypass is never used in CI. To additionally compile Prunt's real generator
and audit tool against the archive, pass
`--consumer-sources /path/to/prunt/config_codegen/src` to the smoke test.

Lightweight checks (no compiler or Libadalang build):

```bash
python3 -m pip install PyYAML==6.0.2
python3 -m unittest discover -s tests -v
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7 -shellcheck= -pyflakes=
```

## Using the index

Add this index to Alire ahead of the community index so its `gnat_native` release can satisfy compiler requirements:

```bash
alr index --add git+https://github.com/Prunt3D/prunt_alire_index.git --name prunt --before community
alr index --update-all
```
