# Emulator patches

The `pcsx-redux/` tree is a gitignored clone of upstream PCSX-Redux. Everything this project
changes in it is captured here as patches.

- `cumulative-vs-upstream-55fbf046.patch` — **the whole delta**, regenerated 2026-08-01 against
  upstream commit `55fbf046`. Apply this one patch to that commit and `make -j4` (in the
  pcsx-redux build container, with a memory cap) reproduces the shipping binary.
- The `*-sessionN.patch` files are historical per-session snapshots kept for the narrative; they
  are not the build input.
