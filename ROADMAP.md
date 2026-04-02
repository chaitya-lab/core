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

- Wire loaded adapter packages into executable kernel dispatch.
- Build first-party system adaptors for registry, file, shell, route, and process.
- Expand tmux integration coverage to restart, stuck detection, and signal handling.
- Add CI on macOS and Linux.
