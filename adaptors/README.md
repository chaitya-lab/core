# Adaptors Workspace

This repository keeps adapter packages under the `adaptors/` directory, even though the public term used by the CLI and SDK is `adapter`.

## Layout

```text
adaptors/
  core/         System adapters
  community/    Optional first-party and community adapters
```

## System Adapters (core/)

These are the repository's system adapters:

- `file` - Read and write local files
- `shell` - Execute one-off shell commands
- `route` - Conditional routing inside pipelines
- `process` - Process inspection and control
- `registry` - Adapter discovery and validation surface

## Community Adapters (community/)

These are optional extensions kept in this repository:

- `browser` - Browser automation via Playwright
- `browser2` - Alternative browser adapter
- `config` - Core and adapter config inspection/editing
- `desktop` - Desktop automation helpers
- `gui` - GUI control
- `test` - Development and test helpers
- `watchdog` - File watching experiments

## Discovery

The kernel discovers adapters from:

- the built-in `adaptors/core/` workspace
- the built-in `adaptors/community/` workspace
- configured filesystem search paths
- installed Python entry points in `chaitya.adapters`

That makes this workspace useful both as a source tree and as a set of reference implementations.

## Adding a New Adapter

1. Choose location: `core/` for system adapters, `community/` for optional adapters.
2. Create adapter structure with `pyproject.toml` and entry point.
3. Add `[project.entry-points."chaitya.adapters"]` in `pyproject.toml`.
4. Install in editable mode for development.

## Moving Adapters Between Core and Community

Keep `core/` narrow. If an adapter is optional, experimental, platform-specific, or replaceable without breaking the kernel surface, it belongs in `community/`.
