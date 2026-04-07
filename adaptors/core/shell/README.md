# Shell Adapter

First-party adapter for executing one-off shell commands.

## Command

```bash
chaitya shell run --command "git status --short"
```

## Behavior

- uses the platform shell
- accepts pipeline input on stdin
- returns stdout plus stderr context when relevant
- rejects some obviously destructive commands
- warns on common interactive terminal programs

## Examples

```bash
chaitya shell run --command "pwd"
chaitya shell run --command "ls -la | head -20"
chaitya file read --path README.md | chaitya shell run --command "grep Chaitya"
```
