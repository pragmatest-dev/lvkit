# Provenance — issue #132 repro

- **Source:** https://github.com/pragmatest-dev/lvkit/issues/132
- **Reporter:** an lvkit user, attached as a bug reproduction.
- **License:** Apache License 2.0 — contributed as a reproduction attached to the
  issue, included here as a regression fixture under the repo's license (see
  `tests/corpus/issues/README.md`).
- **Files:**
  - `Run.vi` (unmodified bytes).
    sha256:
    `e19af6fd8cec2bef8364134ccdf789b7d9b1800b4ac5c4cb7c4a6dac4f904b49`

## What it reproduces

A VI saved with reentrant execution (preallocated clone). LabVIEW records it
with the flag pylabview calls `SaveParallel` and leaves `IsReentrant` clear, so
reading `IsReentrant` alone reported it as non-reentrant. Fixed by reading
reentrancy from both flags. See `test_issue132_save_parallel_vi_reads_as_reentrant`
in `tests/test_issue_corpus.py`.
