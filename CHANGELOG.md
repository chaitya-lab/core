# Changelog

## Unreleased

- Added configurable filesystem adaptor discovery via `adapter_search_paths` and `CHAITYA_ADAPTER_PATHS`.
- Added adaptor suspension/resume handling with `input list` and `input respond`.
- Added kernel-level tests for custom adaptor discovery and resumable input flow.
- Added executable workspace adaptor loading from `adaptors/`.
- Added first-party `file` and `shell` adaptors and wired loaded adaptor handlers into kernel dispatch.
- Added a real `tmux` session backend for macOS/Linux and wired config/kernel selection to it.
- Added repository-root test discovery and opt-in tmux integration tests.
- Added roadmap and `adaptors/` repository structure guidance for future packages.
- Fixed SDK tests for Python 3.14 event-loop behavior.
- Fixed kernel dispatch to preserve pipeline exit codes.
- Fixed kernel session and registry handlers to accept positional CLI arguments.
- Added contributor and adapter development documentation.
