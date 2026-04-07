# Adaptors Workspace

This repository keeps adapter packages under the `adaptors/` directory, even though the public term used by the CLI and SDK is `adapter`.

## Layout

```text
adaptors/
  core/         System adapters (required for kernel boot)
  community/    User/community adapters (optional)
```

## System Adapters (core/)

Required by the kernel per PRD §3.6. Kernel boot fails if any fail to load:

- `file` — Read/write local files
- `shell` — Execute shell commands
- `route` — Conditional routing in pipelines
- `process` — Process management
- `registry` — Adapter discovery and loading

## Community Adapters (community/)

Optional adapters. Kernel warns on load failure but continues:

- `browser` — Browser automation via Playwright
- `browser2` — Alternative browser adapter
- `config` — Configuration management
- `desktop` — Desktop automation (screenshots, clipboard)
- `gui` — GUI control (mouse, keyboard)
- `test` — Testing utilities
- `watchdog` — File watching

## Discovery

During development, the kernel can discover adapters from configured filesystem paths in addition to installed Python entry points.

That makes this workspace useful both as a source tree and as a set of reference implementations.

## Adding a New Adapter

1. Choose location: `core/` for system adapters, `community/` for user adapters
2. Create adapter structure with `pyproject.toml` and entry point
3. Add `[project.entry-points."chaitya.adapters"]` in pyproject.toml
4. Install in editable mode for development

## Moving Adapters Between Core and Community

System adapters that can be replaced should move to `community/`. Core adapters are only those required for kernel boot per the PRD.
