# Code generation for the DDS learning lab.
#
# Why this file exists at all: `idlc` is not simply `idlc` on this host.
#
# The cyclonedds 11.0.1 wheel bundles an idlc binary
# (.venv/lib/python3.13/site-packages/cyclonedds/.libs/idlc), and that binary
# SEGFAULTS on every invocation here, including `--version`. Its ELF headers
# have been rewritten by auditwheel/patchelf (non-PIE EXEC with an extra RW
# PT_LOAD at 0x3ff000 carrying the program headers) and the glibc loader dies
# on them before main. Re-running patchelf over a copy does not repair it.
# The wheel's *shared libraries* are fine — the labs run against them.
#
# So idlc is built from the version-matched upstream source into .tools/,
# which is gitignored, and only CODE GENERATION uses it. The DDS runtime the
# labs link against is still the wheel's libddsc, untouched.
#
#     make tools       build idlc 11.0.1 from source into .tools/  (needs network)
#     make generate    regenerate src/vtslab/generated/ from idl/vts.idl
#     make clean-generated
#
# IDLC is overridable: `make generate IDLC=/usr/bin/idlc` if a working system
# idlc ever appears. Debian trixie's is 0.10.5 — a major version behind the
# wheel's _idlpy backend — so it is not that one today.

REPO    := $(CURDIR)
IDLC    ?= $(REPO)/.tools/prefix/bin/idlc
VENV    := $(REPO)/.venv
GEN_DIR := $(REPO)/src/vtslab/generated
IDL     := $(REPO)/idl/vts.idl

CYCLONE_TAG := 11.0.1

.PHONY: generate tools clean-generated

# Two things about this recipe are not obvious:
#
#  1. It cds into the output directory instead of passing `-o`. idlc's `-o` is
#     accepted and then ignored by the Python backend, which writes its package
#     into the current working directory. Passing `-o` here would silently
#     scatter a vts/ package wherever make was run from.
#
#  2. .venv/bin goes on PATH because `idlc -l py` does not link its Python
#     backend — it shells out to `python3 -m cyclonedds.__idlc__` and dlopens
#     the path that prints. The system python3 has no cyclonedds, so without
#     this the only symptom is "cannot load generator py".
generate:
	@test -x "$(IDLC)" || { echo "no idlc at $(IDLC) — run 'make tools' first"; exit 1; }
	rm -rf "$(GEN_DIR)"
	mkdir -p "$(GEN_DIR)"
	cd "$(GEN_DIR)" && PATH="$(VENV)/bin:$$PATH" "$(IDLC)" -l py "$(IDL)"
	@echo "generated:"
	@find "$(GEN_DIR)" -type f | sort

# The core build supplies idlc and libcycloneddsidl. The Python backend is NOT
# built here: idlc loads the wheel's own _idlpy at run time (see above), and
# that one is a working shared library.
tools:
	git clone -b $(CYCLONE_TAG) --depth 1 \
	    https://github.com/eclipse-cyclonedds/cyclonedds.git \
	    "$(REPO)/.tools/src/cyclonedds"
	cmake -S "$(REPO)/.tools/src/cyclonedds" -B "$(REPO)/.tools/build/cyclonedds" \
	    -DCMAKE_BUILD_TYPE=Release \
	    -DBUILD_TESTING=OFF -DBUILD_IDLC=ON -DBUILD_EXAMPLES=OFF \
	    -DCMAKE_INSTALL_PREFIX="$(REPO)/.tools/prefix"
	cmake --build "$(REPO)/.tools/build/cyclonedds" -j
	cmake --install "$(REPO)/.tools/build/cyclonedds"
	"$(IDLC)" -h > /dev/null && echo "idlc built: $(IDLC)"

clean-generated:
	rm -rf "$(GEN_DIR)"
