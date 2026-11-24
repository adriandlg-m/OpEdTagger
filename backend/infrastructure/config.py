from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

_LOCK = RLock()


def project_root() -> Path:
    """Return the source/application root (the directory containing backend/)."""
    return Path(__file__).resolve().parents[2]


def app_data_dir() -> Path:
    override = os.getenv("OPEDTAGGER_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "OpEdTagger"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpEdTagger"
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "OpEdTagger"


def config_path() -> Path:
    override = os.getenv("OPEDTAGGER_CONFIG_PATH")
    return Path(override).expanduser().resolve() if override else app_data_dir() / "config.json"


def default_config() -> dict[str, Any]:
    data_dir = app_data_dir()
    root = project_root()
    return {
        "library_path": "",
        "template_path": str(root / "Templates"),
        "plugins_path": str(root / "plugins"),
        "database_path": str(data_dir / "opedtagger.sqlite3"),
        "backup_path": str(data_dir / "backups"),
        "log_path": str(data_dir / "logs" / "opedtagger.log"),
        "max_workers": 1,
        "auto_download_templates": False,
    }


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.expanduser().resolve())) == os.path.normcase(str(right.expanduser().resolve()))


def _merge_directory_without_overwrite(source: Path, target: Path) -> None:
    """Copy legacy data into the new root while never replacing existing files."""
    if not source.is_dir() or _same_path(source, target):
        return
    target.mkdir(parents=True, exist_ok=True)
    source_root = source.resolve()
    target_root = target.resolve()
    for item in source.rglob("*"):
        if item.is_symlink():
            continue
        relative = item.relative_to(source_root)
        destination = target_root / relative
        try:
            destination.resolve().relative_to(target_root)
        except ValueError:
            continue
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif item.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Stage each potentially large media/tool file before publishing it.
            # If the process stops mid-copy, the next launch can safely retry.
            temporary = destination.with_name(f".{destination.name}.migration-{uuid.uuid4().hex[:8]}.tmp")
            try:
                shutil.copy2(item, temporary)
                if not destination.exists():
                    temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)


def _persist_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    temp_path.replace(path)


def _migrate_legacy_storage(config: dict[str, Any], stored: dict[str, Any]) -> bool:
    """Move the default data locations to the project root without deleting the source."""
    try:
        migration_version = int(stored.get("_storage_layout_version", 0) or 0)
    except (TypeError, ValueError):
        migration_version = 0
    if migration_version >= 1:
        return False

    root = project_root()
    data_dir = app_data_dir()
    template_target = root / "Templates"
    plugins_target = root / "plugins"

    # The CLI used ./template and ./plugins. The first full-stack release
    # placed both directories under AppData. Merge either source when present.
    _merge_directory_without_overwrite(root / "template", template_target)
    _merge_directory_without_overwrite(root / "templates", template_target)
    _merge_directory_without_overwrite(data_dir / "templates", template_target)
    _merge_directory_without_overwrite(data_dir / "plugins", plugins_target)

    changed = False
    legacy_template_paths = (root / "template", root / "templates", data_dir / "templates")
    legacy_plugin_paths = (data_dir / "plugins",)

    for key, legacy_paths, destination in (
        ("template_path", legacy_template_paths, template_target),
        ("plugins_path", legacy_plugin_paths, plugins_target),
    ):
        raw = stored.get(key)
        if raw is None:
            continue
        configured = Path(str(raw)).expanduser()
        if not configured.is_absolute():
            configured = root / configured
        if any(_same_path(configured, candidate) for candidate in legacy_paths):
            if str(config[key]) != str(destination):
                config[key] = str(destination)
                changed = True

    # Remember that the potentially large copy was completed. Source folders
    # deliberately remain on disk, so this marker avoids rescanning gigabytes
    # of templates on every API request.
    config["_storage_layout_version"] = 1
    return True


def load_config() -> dict[str, Any]:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        stored: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("The configuration root must be an object.")
                stored = loaded
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                raise RuntimeError(f"Could not read configuration file '{path}': {exc}") from exc

        merged = default_config()
        merged.update(stored)
        migrated = _migrate_legacy_storage(merged, stored)

        # Fresh installations also import the original CLI folders, if they
        # happen to exist in the project root.
        if not path.exists() or migrated:
            _persist_config(path, merged)
        return merged


def save_config(changes: dict[str, Any]) -> dict[str, Any]:
    allowed = {"library_path", "template_path", "plugins_path", "backup_path", "auto_download_templates"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"Unsupported settings: {', '.join(sorted(unknown))}")
    with _LOCK:
        cfg = load_config()
        for key, value in changes.items():
            cfg[key] = value
        managed_paths = {}
        for key in ("template_path", "plugins_path", "backup_path"):
            raw = str(cfg.get(key) or "").strip()
            if not raw:
                raise ValueError(f"{key} cannot be empty.")
            managed_paths[key] = Path(raw).expanduser().resolve()
            cfg[key] = str(managed_paths[key])
        library_raw = str(cfg.get("library_path") or "").strip()
        if library_raw:
            library = Path(library_raw).expanduser().resolve()
            if not library.exists() or not library.is_dir():
                raise ValueError("library_path must point to an existing directory.")
            for key, managed_path in managed_paths.items():
                try:
                    managed_path.relative_to(library)
                except ValueError:
                    continue
                raise ValueError(f"{key} must not be located inside the media library; otherwise templates or MKV backups could be indexed as episodes.")
            try:
                app_data_dir().resolve().relative_to(library)
            except ValueError:
                pass
            else:
                raise ValueError("The application data directory must not be located inside the media library.")
            cfg["library_path"] = str(library)
        for managed_path in managed_paths.values():
            managed_path.mkdir(parents=True, exist_ok=True)
        _persist_config(config_path(), cfg)
        return cfg


def resolve_library_path(cfg: dict[str, Any] | None = None, *, must_exist: bool = True) -> Path:
    cfg = cfg or load_config()
    raw = str(cfg.get("library_path") or "").strip()
    if not raw:
        raise ValueError("Library path is not configured. Set it in Settings first.")
    path = Path(raw).expanduser().resolve()
    if must_exist and (not path.exists() or not path.is_dir()):
        raise ValueError(f"Library directory does not exist or is not a directory: {path}")
    return path


def resolve_within(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("The requested path is outside the configured library.") from exc
    return candidate
