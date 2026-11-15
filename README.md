# Prunt Alire Index

This repository builds GCC, a precompiled static Libadalang bundle and GNATprove for Linux x86-64 and ARM64.

## GNATprove

GNATprove analyses Ada with the copy of the GNAT front end inside gnat2why, and computes data representations with
the GCC it bundles, so a released GNATprove rejects code which only Prunt's compiler accepts (for example the
dimension of a range-constrained dimensioned subtype). The `gnatprove` crate here is AdaCore's released GNATprove
(`gnatprove/release.toml`) with gnat2why rebuilt from the `spark2014` submodule against the `gcc` submodule's front
end, and with that compiler, as released in `gnat_native`, in place of the bundled GCC. The driver, Why3, the
provers and SPARKlib are unchanged. `scripts/gnatprove_release.py build` builds and packages it; it refuses a
`gnat_native` release which was not built from the `gcc` submodule's commit, so a compiler update needs a new
`gnatprove` release too, pinned to the new `gnat_native` in `gnatprove/alire.toml`. Its workflow also refuses to
start until that compiler is in the published index: push the compiler release first, or run the GNATprove
workflow again once Build GCC has published it.

The x86-64 archive also contains COLIBRI, a prover which GNATprove supports but does not bundle, from CEA's
release bundle pinned in `gnatprove/release.toml`, under `libexec/spark/colibri`. GNATprove's proof levels do not
use it; `--prover=colibri`, or `--prover=all`, does. Its ECLiPSe engine needs `libnsl.so.1`, which current glibc
no longer provides, but uses nothing from it, so the archive puts an empty library of that name beside it. The
entry for COLIBRI in `share/spark/config/gnatprove.conf` gets the arguments which public COLIBRI releases accept
(`--max-steps` for a steps limit). CEA publishes no ARM64 build, so the aarch64 archive has no COLIBRI.

## Using the index

Add this index to Alire ahead of the community index so its `gnat_native` release can satisfy compiler requirements:

```bash
alr index --add git+https://github.com/Prunt3D/prunt_alire_index.git --name prunt --before community
alr index --update-all
```
