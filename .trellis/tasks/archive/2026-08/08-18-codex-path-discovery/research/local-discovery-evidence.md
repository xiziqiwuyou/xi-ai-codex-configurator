# Local discovery evidence

Evidence collected read-only on Windows on 2026-08-18:

- Runnable command discovered through `PATH`:
  `C:/Users/56252/AppData/Roaming/npm/codex.ps1`.
- The running Microsoft Store Codex package is `OpenAI.Codex` version
  `26.721.4979.0`.
- Its backend process is named `codex.exe`, lives under
  `C:/Program Files/WindowsApps/OpenAI.Codex_.../app/resources/codex.exe`, and
  has an `app-server` command line.
- The desktop parent process is named `ChatGPT.exe`; many renderer/helper
  children share that name and must not be classified as the Codex backend.
- Reading the Store executable path and package registration succeeds, but
  directly running that executable from the script context returns access
  denied. Process evidence therefore cannot imply CLI executability.
- `CODEX_HOME` was unset in the shell and the active local state was present at
  the default `C:/Users/56252/.codex`, including `config.toml` and
  `state_5.sqlite`.
- No process command-line field exposed a custom Codex home. The executable
  parent directory is not a valid configuration target.

Implementation implications:

1. Keep runnable CLI, desktop process, and configuration home as separate
   identities.
2. Require version validation before treating an implicit executable candidate
   as the CLI.
3. Retain access-denied Store paths only as diagnostics/active-process evidence.
4. Detect the desktop backend through the executable identity plus
   `app-server`, not broad substring matching.
5. Require the desktop backend to be closed before local session migration.
