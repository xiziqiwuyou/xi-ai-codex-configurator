# Launch Codex after setup and make the fixed entry lifecycle predictable

## Goal

Make a successful Xi-AI Codex setup visibly finish instead of appearing to
crash, then reopen the detected Codex desktop client with the new configuration.
The fixed one-line entry must keep failures diagnosable and must not terminate a
user's existing interactive PowerShell merely because it was invoked with
`irm ... | iex`.

## Confirmed repository facts

- `scripts/remote_setup.ps1` and `scripts/setup.ps1` currently end with
  `exit $exitCode`.
- The one-line Windows command evaluates the fixed entry through
  `Invoke-Expression`; `exit` therefore closes the hosting PowerShell and makes
  the final output look like a flash/crash.
- `src/codex_configurator/cli.py` already retains the runnable CLI and, when a
  desktop backend was detected, its verified GUI-root executable. It currently
  only prints a restart instruction and never launches Codex.
- Existing Windows evidence shows the Store `OpenAI.Codex` backend under
  `WindowsApps`; direct execution of that observed binary is access-denied, so
  a registered AppX activation path is required for a reliable relaunch.
- Conversation migration may intentionally close the exact desktop instance;
  `N`, `--dry-run`, `--detect-only`, and failed transactions must not launch or
  signal a client.
- The project must not upload local conversation content, tokens, or project
  data. Existing process-identity and rollback gates remain unchanged.

## Requirements

R1. After a successful, real `setup` transaction, request one detached launch of
    the best verified Codex desktop target:

    - when migration closed a desktop instance, use its captured
      `root_executable`, falling back to the backend executable only when the
      root is unavailable;
    - when no instance was closed but a desktop backend is still running, do not
      start a duplicate and report that it is already open;
    - when no backend was running, use an explicitly supplied or
      `desktop-install` executable only when discovery provides one;
    - never launch a plain npm/PATH CLI as a pretend desktop app when no desktop
      evidence exists.

R2. The launch must be non-blocking and detached from the setup terminal on
    Windows, macOS, and Linux. Standard input/output/error must not remain
    attached to the setup process. A successful process-start request prints a
    generic target/PID status and does not print a token or conversation data.

R3. Launch failures must be normalized into a visible setup error after the
    transaction has completed, with a clear manual-start fallback. They must not
    roll back a configuration that was already committed, and they must return a
    non-zero status so unattended callers can detect the issue.

R4. `--dry-run`, `--detect-only`, `status`, `validate`, `restore`, cancelled
    setup, and any setup that fails before commit must not launch Codex.

R5. Fixed PowerShell entries must distinguish file execution from
    `Invoke-Expression`:

    - in `-File` mode, successful completion may close the transient command
      window after the launch status is printed;
    - in `Invoke-Expression` mode, return from the evaluated script instead of
      killing the caller's existing PowerShell; errors remain visible and
      preserve a non-zero result where the host supports it.

R6. Preserve the existing short command, checksum chain, Python-version gate,
    and argument forwarding. Do not add credentials or a second network source.

R7. Add regression coverage for launch target selection, detached process
    invocation, no-launch branches, PowerShell lifecycle branching, and the
    successful setup output. Update the user-facing README and the active
    configurator contract.

## Acceptance criteria

- [x] A mocked successful setup with migration `Y` calls the launcher exactly
      once with the verified desktop root and reports a launch PID.
- [x] A setup with an already-running desktop and migration `N` does not call
      the launcher and says the existing client remains open.
- [x] A CLI-only discovery with no desktop evidence does not detach a CLI and
      instead prints a manual-start instruction.
- [x] Dry-run, detect-only, failed setup, restore, and validation tests show no
      launcher call.
- [x] Windows launch uses detached creation flags and null standard handles;
      POSIX launch uses a new session and null standard handles.
- [x] A Windows Store `WindowsApps` target uses registered AppX activation
      before attempting the inaccessible binary path.
- [x] PowerShell entry tests prove `-File` success exits after completion while
      `Invoke-Expression` success returns without terminating the host.
- [x] Full test suite, PowerShell parse checks, shell syntax checks, and
      `git diff --check` pass.
- [x] README documents the refined prompt and the exact post-success behavior;
      no credential values are added.

## Out of scope

- Rewriting Codex desktop discovery or process identity rules.
- Uploading, copying, or replaying conversation content.
- Changing the Xi-AI endpoint, model catalog semantics, backup format, or
  release protocol.
- Closing an unrelated terminal or forcibly killing a process by name.

## Refined implementation prompt

> 修复 Xi-AI Codex 配置脚本在结束阶段看起来“闪退”的问题，并完善成功后的
> 生命周期：配置事务成功提交后，使用已验证的 Codex 桌面程序路径以脱离当前
> 终端的方式启动一次 Codex，打印“配置完成/启动请求已发送/进程 PID”等可见反馈；
> 如果 Codex 已经运行则不要重复启动；如果只有 CLI、没有桌面证据则提示手动启动。
> `--dry-run`、`--detect-only`、失败或回滚路径不得启动 Codex。固定 PowerShell
> 入口在 `-File` 模式下可在成功并启动后关闭临时命令窗口，但通过
> `irm https://download.xi-ai.net/xi-ai-codex/setup.ps1|iex` 执行时不得用 `exit`
> 杀掉用户当前的 PowerShell；错误必须留在屏幕上并返回失败状态。保留现有校验、
> 备份、迁移和安全边界，补充跨平台单元测试、README 和规范说明。
