from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.request import urlopen

from .catalog import catalog_bytes, load_bundled_catalog, merge_catalog
from .credentials import prompt_token, read_masked_secret
from .discovery import DiscoveryResult, discover
from .endpoints import PROVIDER_ID
from .errors import ConfiguratorError
from .remote_models import fetch_remote_model_ids
from .sessions import collect_rollout_changes, sqlite_columns, sqlite_path
from .toml_merge import merge_config
from .transaction import SetupChanges, apply_setup, latest_backup, restore_backup
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
        output("  警告: 如需选择 Y 迁移对话，请先完全退出 Codex；选择 N 可继续配置")
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


def _choose_session_migration(*, input_fn=input) -> bool:
    value = input_fn(
        "是否让现有本地对话在 xi_ai 下可见？选择 Y 前必须完全退出 Codex [y/N]："
    ).strip().lower()
    return value in {"y", "yes"}


def _read_existing_config(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfiguratorError(f"无法读取 Codex 配置文件：{path}") from exc


def _setup(
    args,
    *,
    input_fn=input,
    secret_fn=read_masked_secret,
    opener=urlopen,
    output=print,
) -> int:
    result = discover(codex_home=args.codex_home, codex_bin=args.codex_bin)
    _print_preflight(result, output)
    if args.detect_only:
        output("探测完成：未请求 API Key，也未写入任何文件。")
        return 0
    token = prompt_token(input_fn=input_fn, secret_fn=secret_fn)
    remote_ids = fetch_remote_model_ids(token, opener=opener)
    bundled = load_bundled_catalog(result.executable, fallback_path=_fallback_catalog())
    merged = merge_catalog(bundled, remote_ids)
    selected_model = _choose_model(remote_ids, input_fn=input_fn, output=output)
    migrate_sessions = _choose_session_migration(input_fn=input_fn)

    if migrate_sessions and result.desktop_process is not None:
        raise ConfiguratorError(
            "检测到 Codex 桌面端仍在运行"
            f"（PID {result.desktop_process.pid}）。选择 Y 会修改本地会话数据库，"
            "为防止数据损坏，脚本已在写入前停止。请完全退出 Codex 后重试，"
            "或重新运行并选择 N。"
        )

    config_path = result.codex_home / "config.toml"
    catalog_path = result.codex_home / "xi-ai-model-catalog.json"
    config_text = merge_config(
        _read_existing_config(config_path),
        model=selected_model,
        catalog_path=catalog_path,
        token=token,
    )
    config_content = config_text.encode("utf-8")
    catalog_content = catalog_bytes(merged)
    parse_toml(config_content)
    parse_catalog(catalog_content)

    rollout_changes = ()
    if migrate_sessions:
        if result.executable is None or result.version is None:
            raise ConfiguratorError(
                "迁移对话需要检测到可运行的 Codex 及其版本"
            )
        database = sqlite_path(result.codex_home)
        columns = sqlite_columns(database)
        if database.is_file() and "model_provider" not in columns:
            raise ConfiguratorError("当前 Codex 会话数据库结构不受支持")
        rollout_changes = tuple(collect_rollout_changes(result.codex_home, PROVIDER_ID))

    output("计划变更：")
    output(f"  默认模型: {selected_model}")
    output(f"  模型总数: {len(merged['models'])}")
    output(f"  迁移现有对话: {'是' if migrate_sessions else '否'}")
    output(f"  待更新会话文件: {len(rollout_changes)}")
    if args.dry_run:
        output("试运行完成：未写入任何文件。")
        return 0

    changes = SetupChanges(
        config_path=config_path,
        config_content=config_content,
        catalog_path=catalog_path,
        catalog_content=catalog_content,
        rollout_changes=rollout_changes,
        migrate_sessions=migrate_sessions,
    )
    backup_dir = apply_setup(result.codex_home, changes)
    validated = validate_installed(result.codex_home)
    output(f"Xi-AI 配置完成，默认模型：{validated['model']}")
    output(f"备份已创建：{backup_dir}")
    if result.desktop_process is not None:
        output("请完全退出并重新启动 Codex，以加载新的供应商配置。")
    else:
        output("请重新启动 Codex，以加载新的供应商配置。")
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
    backup = Path(args.backup).expanduser().resolve() if args.backup else latest_backup(result.codex_home)
    restore_backup(result.codex_home, backup)
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
        if name == "restore":
            subparser.add_argument("--backup", help="恢复指定的备份目录")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    input_fn=input,
    secret_fn=read_masked_secret,
    opener=urlopen,
    output=print,
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
