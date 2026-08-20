from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.request import urlopen

from .catalog import catalog_bytes, load_bundled_catalog, merge_catalog
from .credentials import prompt_token, read_masked_secret
from .desktop_control import close_codex_desktop
from .discovery import (
    DiscoveryResult,
    discover,
    discover_running_codex_processes,
)
from .endpoints import PROVIDER_ID
from .errors import BackupSpaceError, ConfiguratorError
from .launcher import CodexLaunchResult, launch_codex, select_launch_target
from .progress import ConsoleProgress
from .remote_models import fetch_remote_model_ids
from .sessions import collect_rollout_changes, sqlite_columns, sqlite_path
from .toml_merge import (
    CLEAR_CONTEXT,
    CONTEXT_1M,
    CONTEXT_500K,
    PRESERVE_CONTEXT,
    ContextConfig,
    merge_config,
)
from .transaction import (
    SetupChanges,
    apply_setup,
    candidate_backup_roots,
    check_backup_space,
    latest_backup,
    restore_backup,
)
from .validation import parse_catalog, parse_toml, validate_installed


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _fallback_catalog() -> Path:
    return _project_root() / "assets" / "bundled-models.json"


def _print_preflight(result: DiscoveryResult, output=print) -> None:
    output("Codex 配置预检")
    output(
        f"  可运行 CLI: {result.executable or '未找到'} "
        f"（来源={result.executable_source}）"
    )
    output(f"  CLI 版本: {result.version or '未知'}")
    if result.desktop_process is None:
        output("  桌面后端: 未检测到")
    else:
        process = result.desktop_process
        output(
            f"  桌面后端: {process.executable} "
            f"（来源={process.source}, PID={process.pid}, 状态=运行中）"
        )
        if process.root_pid is not None and process.root_pid != process.pid:
            output(
                f"  桌面窗口: {process.root_executable or '路径未知'} "
                f"（根 PID={process.root_pid}）"
            )
        output("  提示: 选择 Y 后脚本将自动关闭此 Codex 实例；选择 N 不会关闭")
    markers = ", ".join(result.home_markers) or "无"
    output(
        f"  CODEX_HOME: {result.codex_home} "
        f"（来源={result.codex_home_source}, 置信度={result.home_confidence}, "
        f"标记={markers}）"
    )
    output(f"  配置文件: {result.codex_home / 'config.toml'}")
    output(f"  模型目录: {result.codex_home / 'xi-ai-model-catalog.json'}")
    output(f"  会话目录: {result.codex_home / 'sessions'}")
    output(f"  已归档会话: {result.codex_home / 'archived_sessions'}")
    output(f"  会话数据库: {sqlite_path(result.codex_home)}")
    for warning in result.warnings:
        output(f"  警告: {warning}")


def _choose_model(model_ids: list[str], *, input_fn=input, output=print) -> str:
    output("可用的 Xi-AI 模型：")
    for index, model_id in enumerate(model_ids, start=1):
        output(f"  {index}. {model_id}")
    while True:
        raw = input_fn("请选择默认模型编号：").strip()
        try:
            selected = int(raw)
        except ValueError:
            selected = 0
        if 1 <= selected <= len(model_ids):
            return model_ids[selected - 1]
        output("请输入有效的模型编号。")


LONG_CONTEXT_MODELS = frozenset(
    {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
)


def _context_summary(context: ContextConfig) -> str:
    if context.mode == "preserve":
        return "保留现有设置"
    if context.mode == "clear":
        return "恢复 Codex 默认"
    assert context.model_context_window is not None
    assert context.model_auto_compact_token_limit is not None
    window_label = (
        "1M"
        if context.model_context_window == 1_000_000
        else f"{context.model_context_window // 1000}K"
    )
    return (
        f"{window_label} 上下文"
        f"（自动压缩阈值 {context.model_auto_compact_token_limit // 1000}K）"
    )


def _choose_context_config(
    model: str, *, input_fn=input, output=print
) -> ContextConfig:
    if model not in LONG_CONTEXT_MODELS:
        return PRESERVE_CONTEXT

    output(f"模型 {model} 支持手动长上下文配置：")
    output("  1. 保留现有设置（默认）")
    output("  2. 500K（窗口 500K，自动压缩阈值 450K）")
    output("  3. 1M（窗口 1M，自动压缩阈值 900K）")
    output("  4. 恢复 Codex 默认（删除长上下文配置）")
    output(
        "提示：更大的上下文可能增加额度消耗；超过 272K 的计费规则"
        "以服务方当前说明为准。"
    )
    while True:
        raw = input_fn("请选择上下文配置 [1]：").strip().lower()
        if raw in {"", "1"}:
            return PRESERVE_CONTEXT
        if raw == "2":
            return CONTEXT_500K
        if raw == "3":
            return CONTEXT_1M
        if raw == "4":
            return CLEAR_CONTEXT
        output("请输入 1、2、3 或 4。")


def _choose_session_migration(*, input_fn=input) -> bool:
    value = input_fn(
        "是否让现有本地对话在 xi_ai 下可见？选择 Y 将自动关闭 Codex [y/N]："
    ).strip().lower()
    return value in {"y", "yes"}


def _require_no_desktop_processes(detector, *, after_close: bool) -> None:
    remaining, warnings = detector()
    if warnings:
        raise ConfiguratorError(
            "无法重新检查 Codex 桌面进程，已停止配置"
        )
    if remaining:
        state = "已重新出现" if after_close else "仍在运行"
        raise ConfiguratorError(
            f"Codex 桌面后端{state}"
            f"（PID {remaining[0].pid}），已在写入前停止配置"
        )


def _choose_backup_root(
    codex_home: Path,
    changes: SetupChanges,
    requested: str | None,
    *,
    input_fn=input,
    output=print,
) -> Path | None:
    explicit = Path(requested).expanduser() if requested else None
    try:
        check_backup_space(codex_home, changes, explicit)
        return explicit
    except BackupSpaceError as exc:
        if explicit is not None:
            raise ConfiguratorError(str(exc)) from exc
        output(f"{exc}")
        candidates = candidate_backup_roots(codex_home, changes)
        if candidates:
            output("可用的备用备份目录建议：")
            for candidate in candidates:
                output(f"  {candidate}")
        else:
            output("未自动找到有足够空间的备用磁盘。")
        selected = input_fn(
            "请输入备用备份目录（回车取消，取消将不会修改任何文件）："
        ).strip()
        if not selected:
            raise ConfiguratorError("未选择备用备份目录，已停止配置") from exc
        fallback = Path(selected).expanduser()
        try:
            check_backup_space(codex_home, changes, fallback)
        except BackupSpaceError as fallback_exc:
            raise ConfiguratorError(str(fallback_exc)) from fallback_exc
        return fallback


def _read_existing_config(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfiguratorError(f"无法读取 Codex 配置文件：{path}") from exc


def _report_post_commit_launch(
    discovery: DiscoveryResult,
    *,
    was_closed: bool,
    launcher=None,
    output=print,
) -> None:
    """Report or request the one permitted post-commit desktop launch."""

    if not was_closed and discovery.desktop_process is not None:
        output(
            "Codex 桌面客户端仍在运行，已保留现有实例；请完全退出后重新启动以加载新配置。"
        )
        return

    launch_target = select_launch_target(discovery, was_closed=was_closed)
    if launch_target is None:
        output("未检测到可自动启动的 Codex 桌面程序；配置已提交，请手动启动 Codex。")
        return

    try:
        launch_callable = launch_codex if launcher is None else launcher
        launch_result: CodexLaunchResult = launch_callable(
            discovery,
            was_closed=was_closed,
        )
    except ConfiguratorError:
        raise
    except Exception as exc:
        raise ConfiguratorError(
            "配置已提交，但 Codex 启动请求失败；请手动启动 Codex。"
        ) from exc
    output(
        f"配置已提交，启动请求已发送（PID {launch_result.pid}）；"
        f"目标：{launch_result.target}"
    )


def _setup(
    args,
    *,
    input_fn=input,
    secret_fn=read_masked_secret,
    opener=urlopen,
    output=print,
    desktop_closer=None,
    process_detector=None,
    codex_launcher=None,
) -> int:
    discovery_options = {}
    if process_detector is not None:
        discovery_options["process_detector"] = process_detector
    result = discover(
        codex_home=args.codex_home,
        codex_bin=args.codex_bin,
        **discovery_options,
    )
    _print_preflight(result, output)
    if args.detect_only:
        output("探测完成：未请求 API Key，也未写入任何文件。")
        return 0
    token = prompt_token(input_fn=input_fn, secret_fn=secret_fn)
    remote_ids = fetch_remote_model_ids(token, opener=opener)
    bundled = load_bundled_catalog(result.executable, fallback_path=_fallback_catalog())
    merged = merge_catalog(bundled, remote_ids)
    selected_model = _choose_model(remote_ids, input_fn=input_fn, output=output)
    context = _choose_context_config(
        selected_model, input_fn=input_fn, output=output
    )
    migrate_sessions = _choose_session_migration(input_fn=input_fn)

    desktop_was_closed = False
    detect_processes = process_detector or discover_running_codex_processes
    if migrate_sessions:
        if result.executable is None or result.version is None:
            raise ConfiguratorError("迁移对话需要检测到可运行的 Codex 及其版本")
        if result.desktop_process is not None:
            process = result.desktop_process
            if args.dry_run:
                output(
                    "试运行提示：正式执行时将自动关闭 Codex "
                    f"（后端 PID {process.pid}），正常退出超时后会精确强制终止。"
                )
            else:
                output(
                    "正在关闭 Codex 桌面端"
                    f"（后端 PID {process.pid}），请勿重新打开客户端..."
                )
                closer = desktop_closer or close_codex_desktop
                close_result = closer(process)
                if close_result.forced:
                    output(
                        "Codex 未在 15 秒内正常退出，已完成精确强制终止"
                        f"（根 PID {close_result.root_pid}）。"
                    )
                else:
                    output(
                        "Codex 已正常退出"
                        f"（根 PID {close_result.root_pid}）。"
                    )
                desktop_was_closed = True
        if not args.dry_run:
            _require_no_desktop_processes(
                detect_processes,
                after_close=desktop_was_closed,
            )

    config_path = result.codex_home / "config.toml"
    catalog_path = result.codex_home / "xi-ai-model-catalog.json"
    config_text = merge_config(
        _read_existing_config(config_path),
        model=selected_model,
        catalog_path=catalog_path,
        token=token,
        context=context,
    )
    config_content = config_text.encode("utf-8")
    catalog_content = catalog_bytes(merged)
    parse_toml(config_content)
    parse_catalog(catalog_content)

    rollout_changes = ()
    session_progress = ConsoleProgress(output=output) if migrate_sessions else None
    if migrate_sessions:
        database = sqlite_path(result.codex_home)
        columns = sqlite_columns(database)
        if database.is_file() and "model_provider" not in columns:
            raise ConfiguratorError("当前 Codex 会话数据库结构不受支持")
        rollout_changes = tuple(
            collect_rollout_changes(
                result.codex_home,
                PROVIDER_ID,
                progress=session_progress,
            )
        )

    output("计划变更：")
    output(f"  默认模型: {selected_model}")
    output(f"  上下文配置: {_context_summary(context)}")
    output(f"  模型总数: {len(merged['models'])}")
    output(f"  迁移现有对话: {'是' if migrate_sessions else '否'}")
    output(f"  待更新会话文件: {len(rollout_changes)}")
    changes = SetupChanges(
        config_path=config_path,
        config_content=config_content,
        catalog_path=catalog_path,
        catalog_content=catalog_content,
        rollout_changes=rollout_changes,
        migrate_sessions=migrate_sessions,
    )
    try:
        check_backup_space(
            result.codex_home,
            changes,
            Path(args.backup_root).expanduser() if args.backup_root else None,
        )
        output("  预计备份空间检查：通过")
    except BackupSpaceError as exc:
        if args.dry_run:
            output(f"  试运行提示：{exc}")
    if args.dry_run:
        output("试运行完成：未写入任何文件。")
        return 0

    if migrate_sessions:
        _require_no_desktop_processes(
            detect_processes,
            after_close=True,
        )

    backup_root = _choose_backup_root(
        result.codex_home,
        changes,
        args.backup_root,
        input_fn=input_fn,
        output=output,
    )
    backup_dir = apply_setup(
        result.codex_home,
        changes,
        allow_wal_recovery=migrate_sessions,
        backup_root=backup_root,
        progress=session_progress,
    )
    validated = validate_installed(result.codex_home)
    output(f"Xi-AI 配置完成，默认模型：{validated['model']}")
    output(f"备份已创建：{backup_dir}")
    _report_post_commit_launch(
        result,
        was_closed=desktop_was_closed,
        launcher=codex_launcher,
        output=output,
    )
    return 0


def _status(args, *, output=print) -> int:
    result = discover(codex_home=args.codex_home, codex_bin=args.codex_bin)
    _print_preflight(result, output)
    try:
        validated = validate_installed(result.codex_home)
    except ConfiguratorError as exc:
        output(f"Xi-AI 状态：未配置（{exc}）")
        return 1
    output(f"Xi-AI 状态：已配置，模型={validated['model']}")
    output(f"模型目录数量：{validated['model_count']}")
    return 0


def _validate(args, *, output=print) -> int:
    result = discover(codex_home=args.codex_home, codex_bin=args.codex_bin)
    validated = validate_installed(result.codex_home)
    output(
        f"验证通过：供应商={validated['provider']}, "
        f"模型={validated['model']}, 模型数量={validated['model_count']}"
    )
    return 0


def _restore(args, *, output=print) -> int:
    result = discover(codex_home=args.codex_home, codex_bin=args.codex_bin)
    requested_root = (
        Path(args.backup_root).expanduser().resolve() if args.backup_root else None
    )
    backup = (
        Path(args.backup).expanduser().resolve()
        if args.backup
        else latest_backup(result.codex_home, requested_root)
    )
    restore_root = requested_root
    if args.backup and restore_root is None:
        restore_root = backup.parent
    restore_backup(result.codex_home, backup, backup_root=restore_root)
    output(f"已恢复 Xi-AI 备份：{backup}")
    output("请重启 Codex 后再继续使用。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xi-ai-codex", description="将 Codex 配置为使用 Xi-AI。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "status", "validate", "restore"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--codex-home", help="覆盖 CODEX_HOME 路径")
        subparser.add_argument("--codex-bin", help="覆盖 Codex 可执行文件路径")
        if name == "setup":
            subparser.add_argument("--dry-run", action="store_true")
            subparser.add_argument(
                "--detect-only",
                action="store_true",
                help="仅探测 Codex 路径和进程，不请求 Key，也不写入文件",
            )
            subparser.add_argument(
                "--backup-root",
                help="指定备份根目录，可放在其他磁盘",
            )
        if name == "restore":
            subparser.add_argument("--backup", help="恢复指定的备份目录")
            subparser.add_argument(
                "--backup-root",
                help="从指定备份根目录恢复最新备份",
            )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    input_fn=input,
    secret_fn=read_masked_secret,
    opener=urlopen,
    output=print,
    desktop_closer=None,
    process_detector=None,
    codex_launcher=None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            return _setup(
                args,
                input_fn=input_fn,
                secret_fn=secret_fn,
                opener=opener,
                output=output,
                desktop_closer=desktop_closer,
                process_detector=process_detector,
                codex_launcher=codex_launcher,
            )
        if args.command == "status":
            return _status(args, output=output)
        if args.command == "validate":
            return _validate(args, output=output)
        return _restore(args, output=output)
    except ConfiguratorError as exc:
        output(f"错误：{exc}")
        return 1
    except KeyboardInterrupt:
        output("已取消。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
