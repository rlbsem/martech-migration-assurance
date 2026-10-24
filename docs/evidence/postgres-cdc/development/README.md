# Preserved development diagnostics

These records are excluded from final qualification. [Diagnostics](diagnostics.json) identify actual failures, their private original log hashes and the correction. [Failing JUnit record](failed-comparison.xml) removes only the runner hostname and repository-specific absolute path. The private originals remain ignored.

The [development protocol](protocol.json) and [derived outcomes](results.json) preserve the initial 13-scenario exercise with uncommitted source. Its explicit `qualified_source=false` / `development_only` status remains unchanged. It is not proof of the later frozen implementation, and its source fingerprints must not be relabeled to match that implementation. The final [qualified native evidence](../native/protocol.json) is a separate fresh execution.

No original SQLite evidence was edited. New diagnostics and final results are stored separately rather than overwriting earlier outcomes.
