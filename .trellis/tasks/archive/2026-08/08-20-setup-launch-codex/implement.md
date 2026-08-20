# Implementation plan

1. Load the backend and configurator contract, then activate this task after
   the planning review gate.
2. Add the launcher data model, target-selection helper, and detached
   cross-platform process start implementation.
3. Integrate the launcher into the successful CLI setup path with explicit
   no-launch branches and injectable test seams.
4. Update both PowerShell entry scripts to branch on `-File` versus evaluated
   execution and keep error output visible.
5. Add focused CLI/launcher/entry-script regression tests; update README and
   `.trellis/spec/backend/codex-configurator.md` with the lifecycle contract.
6. Run targeted tests, then the full suite, Python compile checks, PowerShell
   parser checks, POSIX shell syntax checks, and `git diff --check`.
7. Review the complete diff, prepare one coherent work commit, and leave FTP
   publishing for an explicit release request.

## Validation commands

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests -v
python -m compileall -q src scripts tests
sh -n scripts/setup.sh
sh -n scripts/remote_setup.sh
git diff --check
```

PowerShell parsing will be run with
`[System.Management.Automation.Language.Parser]::ParseFile` for both entry
scripts, and a Windows-only subprocess test will exercise the packaged fixed
entry when the host provides Windows PowerShell 5.1.

## Rollback points

- Before the CLI integration, the launcher is isolated and removable.
- Before entry-script changes, the Python tests must be green.
- If PowerShell host behavior cannot be proven on the target runtime, retain
  the safe no-`exit` IEX branch and document the validation gap rather than
  restoring unconditional host termination.
