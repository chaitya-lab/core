# Adaptors Workspace

This repository keeps adapter packages under the `adaptors/` directory, even though the public term used by the CLI and SDK is `adapter`.

## Layout

```text
adaptors/
  core/         First-party adapters maintained with the kernel
  community/    Experimental and community adapters
```

## Purpose

This workspace exists to:

- ship first-party adapters alongside the kernel
- develop adapters locally without publishing them first
- provide examples for adapter authors

## Core Adapters In This Repo

- `file`
- `shell`
- `route`
- `process`
- `test`
- `desktop`
- `gui`

## Community Adapters In This Repo

- `browser`
- `browser2`

## Discovery

During development, the kernel can discover adapters from configured filesystem paths in addition to installed Python entry points.

That makes this workspace useful both as a source tree and as a set of reference implementations.
