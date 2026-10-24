# First qualified local run at 9910457

These observed bytes and manifests are preserved from the first completed local native run at `99104575c683f2b1e3287937141b182b7c89b094`. All 70 tests and 13 native scenarios passed. The native implementation was working; later review corrected the CI container/loopback port mapping and clarified report attribution. No failed cloud or vendor execution is implied.

The [original protocol](native/protocol.json), [cases and artifact manifest](native/manifest.json), [software record](software/verification.json), [report](report.md) and [cleanup read](cleanup.json) retain their original bytes. Relative report links remain valid inside this archive. The native source files are available at the pinned Git revision. Run the standalone checker without `--source-root` for raw-data integrity, or point that option at a separate checkout of the pinned revision. This archive must not be relabeled to match later workflow/reporter source hashes.

The [current native acceptance](../postgres-cdc/native/protocol.json) is a separate fresh execution after the tooling correction.
