# Roadmap

## Kernel

- Keep the core limited to the seven kernel responsibilities from `prd.md`.
- Preserve protocol boundaries so session, store, event bus, and pipeline implementations stay replaceable.
- Treat macOS/Linux `tmux` support as the reference PTY substrate for future runtimes.

## Adaptors

This repository uses `adapter` in code and contracts, but keeps future packages under a top-level `adaptors/` workspace for contributor organization.

Recommended layout:

```text
adaptors/
  core/<adaptor-name>/        # first-party maintained adaptors
  community/<author>/<name>/  # third-party or experimental adaptors
```

This gives contributors a predictable place to add packages without polluting the kernel root.

## Near-Term Work

- Expand first-party adaptors beyond `file` and `shell` once their contracts settle.
- Decide whether `route` and `process` stay first-party workspace adaptors or remain future external packages.
- Expand tmux integration coverage to restart, stuck detection, and signal handling.
- Add CI on macOS and Linux.
