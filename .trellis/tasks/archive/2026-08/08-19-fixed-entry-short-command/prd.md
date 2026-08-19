# Fixed-entry short command

## Goal

Make the Windows default setup entry easy to copy and remember:

```powershell
irm https://download.xi-ai.net/xi-ai-codex/setup.ps1|iex
```

## Background

- The fixed `setup.ps1` is deployed and has passed a native Windows PowerShell 5.1 test; without arguments it enters interactive configuration and asks for the API key.
- The fixed entry resolves `latest.json` and validates the release manifest, Bootstrap size, manifest SHA-256, and independent checksum; Bootstrap validates the release bundle too.
- The user explicitly accepts trusting the fixed entry bytes returned over HTTPS/TLS without an additional outer checksum step.

## Requirements

1. README presents the exact command above as the default and primary Windows setup path.
2. README explains the short-mode trust boundary: HTTPS/TLS protects the fixed entry, while all subsequent release assets retain their existing verification.
3. The existing download-plus-checksum command remains and is labeled strict checksum mode.
4. Documentation and tests contain no FTPS username, password, API key, or other credential values.
5. Tests lock the default short command and confirm strict Windows and POSIX checksum modes remain available.

## Acceptance Criteria

- [x] README contains the exact approved Windows default command.
- [x] The public short command reaches the interactive configuration prompt in a real PowerShell process.
- [x] README immediately explains the HTTPS/TLS trust boundary and downstream asset verification.
- [x] The original SHA-256 command remains labeled strict checksum mode.
- [x] Documentation contract tests, the full test suite, and static checks pass.

## Out of Scope

- Do not change the API domain, models, context settings, key input, backup, conversation visibility, or Codex path discovery.
- Do not shorten the macOS/Linux command; it remains checksum-first.
- Do not add another domain or URL route.
- Do not remove the existing fixed entry, checksum files, or immutable release directories.

