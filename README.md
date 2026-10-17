# Prunt Alire Index

This repository builds GCC and a precompiled static Libadalang bundle for Linux x86-64 and ARM64.

## Using the index

Add this index to Alire ahead of the community index so its `gnat_native` release can satisfy compiler requirements:

```bash
alr index --add git+https://github.com/Prunt3D/prunt_alire_index.git --name prunt --before community
alr index --update-all
```
