# Adaptors Workspace

The kernel API and docs use the term `adapter`, because that is the contract name exposed to users and packages.

This repository uses the folder name `adaptors/` as a workspace for first-party packages:

```text
adaptors/
  core/<adaptor-name>/          # first-party: file, shell
  community/<author>/<name>/   # third-party or experimental
```

The `core/` adapters (`file`, `shell`) are discovered from this workspace during development.

The `registry` command is built into the kernel — no separate pip package needed.

Use `community/` for experimental or third-party packages, grouped by maintainer or organization.
