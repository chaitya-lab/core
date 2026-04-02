# Adaptors Workspace

The kernel API and docs use the term `adapter`, because that is the contract name exposed to users and packages.

This repository uses the folder name `adaptors/` as a workspace for future package organization:

```text
adaptors/
  core/<adaptor-name>/
  community/<author>/<adaptor-name>/
```

Use `core/` for first-party packages that define the baseline Chaitya ecosystem.

Use `community/` for experimental or third-party packages, grouped by maintainer or organization.

This folder is intentionally documentation-first for now. The kernel remains the only implemented package in this repository.
