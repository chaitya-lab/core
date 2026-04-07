# File Adapter

First-party adapter for reading and writing local UTF-8 text files.

## Commands

```bash
chaitya file read --path README.md
chaitya file write --path notes.txt --text "hello"
```

If the write targets an existing file or a dangerous location, the adapter asks for explicit confirmation:

```bash
chaitya file write --path notes.txt --text "replace" --confirm
```

## Notes

- permissions are enforced through the SDK
- reads and writes are limited to declared paths
- writes create parent directories when needed
