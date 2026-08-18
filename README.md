# Prunt Alire Index

This repository hosts a private Alire index for x86-64 and ARM64 GCC builds with C, Ada, and Fortran enabled, produced from the `Prunt3D/gcc` fork tracked as the `gcc/` submodule.

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
and commits the updated manifest back.

## Using the index

Add this index to Alire ahead of the community index so its `gnat_native` release can satisfy compiler requirements:

```bash
alr index --add git+https://github.com/Prunt3D/prunt_alire_index.git --name prunt --before community
alr index --update-all
```
