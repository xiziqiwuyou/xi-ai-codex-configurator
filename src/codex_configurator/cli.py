from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from urllib.request import urlopen

from .catalog import catalog_bytes, load_bundled_catalog, merge_catalog
from .credentials import prompt_token
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
    output("Codex preflight")
    output(f"  executable: {result.executable or 'not found'}")
    output(f"  version: {result.version or 'unknown'}")
    output(f"  CODEX_HOME: {result.codex_home}")
    output(f"  config: {result.codex_home / 'config.toml'}")
    output(f"  model catalog: {result.codex_home / 'xi-ai-model-catalog.json'}")
    output(f"  sessions: {result.codex_home / 'sessions'}")
    output(f"  archived sessions: {result.codex_home / 'archived_sessions'}")
    output(f"  session database: {sqlite_path(result.codex_home)}")


def _choose_model(model_ids: list[str], *, input_fn=input, output=print) -> str:
    output("Available Xi-AI models:")
    for index, model_id in enumerate(model_ids, start=1):
        output(f"  {index}. {model_id}")
    while True:
        raw = input_fn("Select the default model number: ").strip()
        try:
            selected = int(raw)
        except ValueError:
            selected = 0
        if 1 <= selected <= len(model_ids):
            return model_ids[selected - 1]
        output("Enter a valid model number.")


def _choose_session_migration(*, input_fn=input) -> bool:
    value = input_fn(
        "Make existing local conversations visible under xi_ai? [y/N]: "
    ).strip().lower()
    return value in {"y", "yes"}


def _read_existing_config(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfiguratorError(f"Unable to read Codex config: {path}") from exc


def _setup(
    args,
    *,
    input_fn=input,
    secret_fn=getpass.getpass,
    opener=urlopen,
    output=print,
) -> int:
    result = discover(codex_home=args.codex_home, codex_bin=args.codex_bin)
    _print_preflight(result, output)
    token = prompt_token(input_fn=input_fn, secret_fn=secret_fn)
    remote_ids = fetch_remote_model_ids(token, opener=opener)
    bundled = load_bundled_catalog(result.executable, fallback_path=_fallback_catalog())
    merged = merge_catalog(bundled, remote_ids)
    selected_model = _choose_model(remote_ids, input_fn=input_fn, output=output)
    migrate_sessions = _choose_session_migration(input_fn=input_fn)

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
                "A detected Codex executable and version are required for conversation migration"
            )
        database = sqlite_path(result.codex_home)
        columns = sqlite_columns(database)
        if database.is_file() and "model_provider" not in columns:
            raise ConfiguratorError("Unsupported Codex session database schema")
        rollout_changes = tuple(collect_rollout_changes(result.codex_home, PROVIDER_ID))

    output("Planned changes:")
    output(f"  selected model: {selected_model}")
    output(f"  catalog models: {len(merged['models'])}")
    output(f"  migrate conversations: {'yes' if migrate_sessions else 'no'}")
    output(f"  rollout files to update: {len(rollout_changes)}")
    if args.dry_run:
        output("Dry run complete; no files were written.")
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
    output(f"Configured Xi-AI model: {validated['model']}")
    output(f"Backup created: {backup_dir}")
    if migrate_sessions:
        output("Restart Codex to refresh local conversation visibility.")
    return 0


def _status(args, *, output=print) -> int:
    result = discover(codex_home=args.codex_home, codex_bin=args.codex_bin)
    _print_preflight(result, output)
    try:
        validated = validate_installed(result.codex_home)
    except ConfiguratorError as exc:
        output(f"Xi-AI status: not configured ({exc})")
        return 1
    output(f"Xi-AI status: configured, model={validated['model']}")
    output(f"Catalog models: {validated['model_count']}")
    return 0


def _validate(args, *, output=print) -> int:
    result = discover(codex_home=args.codex_home, codex_bin=args.codex_bin)
    validated = validate_installed(result.codex_home)
    output(
        f"Validation passed: provider={validated['provider']}, "
        f"model={validated['model']}, models={validated['model_count']}"
    )
    return 0


def _restore(args, *, output=print) -> int:
    result = discover(codex_home=args.codex_home, codex_bin=args.codex_bin)
    backup = Path(args.backup).expanduser().resolve() if args.backup else latest_backup(result.codex_home)
    restore_backup(result.codex_home, backup)
    output(f"Restored Xi-AI backup: {backup}")
    output("Restart Codex before resuming work.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xi-ai-codex", description="Configure Codex to use Xi-AI."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("setup", "status", "validate", "restore"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--codex-home", help="Override CODEX_HOME")
        subparser.add_argument("--codex-bin", help="Override the Codex executable")
        if name == "setup":
            subparser.add_argument("--dry-run", action="store_true")
        if name == "restore":
            subparser.add_argument("--backup", help="Restore a specific backup directory")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    input_fn=input,
    secret_fn=getpass.getpass,
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
        output(f"Error: {exc}")
        return 1
    except KeyboardInterrupt:
        output("Cancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
