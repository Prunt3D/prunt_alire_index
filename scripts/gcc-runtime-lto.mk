# Top-level GCC make clears MAKEOVERRIDES, so command-line GNATLIBCFLAGS alone
# never reaches libada. Forward it explicitly through build and install.
GNATLIBCFLAGS = -g -O2 -flto -ffat-lto-objects
BASE_FLAGS_TO_PASS += "GNATLIBCFLAGS=$(GNATLIBCFLAGS)"

# GNATLIBCFLAGS covers both Ada units and C support code. GCC's shared-runtime
# recipe still appends -fno-lto; only the ordinary static archives contain LTO.
