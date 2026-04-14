# Prunt Alire Index

This repository hosts a private Alire index for a GCC/GNAT build produced from the `Prunt3D/gcc` fork tracked as the `gcc/` submodule.

## Release flow

1. Update the `gcc/` submodule to the desired compiler revision.
2. Edit `release.toml` and choose the Alire crate version to publish.
3. Push the submodule bump and version change.
4. GitHub Actions builds GCC out of tree, smoke-tests it, uploads release assets, regenerates the index manifest, and commits the updated manifest back.

## Using the index

Add this index to Alire ahead of the community index so its `gnat_native` release can satisfy compiler requirements:

```bash
alr index --add git+https://github.com/Prunt3D/prunt_alire_index.git --name prunt --before community
alr index --update-all
```