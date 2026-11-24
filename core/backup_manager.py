from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterable

BACKUP_LAYOUT = ("temporary", "recovery", "chapters")
MINIMUM_FREE_RESERVE_BYTES = 256 * 1024 * 1024
BACKUP_RESERVE_RATIO = 0.01


def ensure_backup_layout(backup_root: Path) -> dict[str, Path]:
    root = backup_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    directories = {name: root / name for name in BACKUP_LAYOUT}
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    return directories


def write_transaction_manifest(transaction_dir: Path, manifest: dict[str, Any]) -> Path:
    transaction_dir.mkdir(parents=True, exist_ok=True)
    path = transaction_dir / "manifest.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return path


def read_transaction_manifest(transaction_dir: Path) -> dict[str, Any]:
    path = transaction_dir / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload.get("transaction_id"):
        raise ValueError(f"Invalid backup transaction manifest: {path}")
    return payload


def move_transaction_to_recovery(transaction_dir: Path, backup_root: Path, *, reason: str) -> Path:
    directories = ensure_backup_layout(backup_root)
    transaction_dir = transaction_dir.resolve()
    temporary_root = directories["temporary"].resolve()
    try:
        transaction_dir.relative_to(temporary_root)
    except ValueError as exc:
        raise ValueError("Only a temporary backup transaction can be moved to recovery.") from exc

    manifest = read_transaction_manifest(transaction_dir)
    manifest["phase"] = "RECOVERY_REQUIRED"
    manifest["recovery_reason"] = reason
    write_transaction_manifest(transaction_dir, manifest)

    transaction_id = str(manifest["transaction_id"])
    destination = directories["recovery"] / transaction_id
    if destination.exists():
        destination = directories["recovery"] / f"{transaction_id}-{uuid.uuid4().hex[:8]}"
    transaction_dir.replace(destination)
    return destination


def estimate_sequential_backup_space(files: Iterable[Path], backup_root: Path) -> dict[str, Any]:
    paths = [Path(item).expanduser().resolve() for item in files]
    if not paths:
        raise ValueError("No MKV files were supplied for backup-space estimation.")
    sizes: list[tuple[Path, int]] = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"MKV file is unavailable: {path}")
        sizes.append((path, path.stat().st_size))

    largest_path, largest_size = max(sizes, key=lambda item: item[1])
    reserve = max(MINIMUM_FREE_RESERVE_BYTES, int(largest_size * BACKUP_RESERVE_RATIO))
    required = largest_size + reserve
    directories = ensure_backup_layout(backup_root)
    usage = shutil.disk_usage(directories["temporary"])
    return {
        "selected_count": len(paths),
        "selected_bytes": sum(size for _path, size in sizes),
        "largest_file": largest_path.name,
        "largest_file_bytes": largest_size,
        "reserve_bytes": reserve,
        "required_free_bytes": required,
        "available_free_bytes": usage.free,
        "enough_space": usage.free >= required,
        "backup_path": str(Path(backup_root).expanduser().resolve()),
        "temporary_path": str(directories["temporary"]),
        "processing_mode": "sequential",
    }


def assert_backup_space(file_path: Path, backup_root: Path) -> dict[str, Any]:
    estimate = estimate_sequential_backup_space([file_path], backup_root)
    if not estimate["enough_space"]:
        required = estimate["required_free_bytes"] / (1024 ** 3)
        available = estimate["available_free_bytes"] / (1024 ** 3)
        raise OSError(
            "Insufficient free space for a safe temporary MKV backup. "
            f"Required approximately {required:.2f} GiB; available {available:.2f} GiB "
            f"on the backup volume ({estimate['backup_path']})."
        )
    return estimate


def _directory_size(path: Path) -> int:
    total = 0
    if not path.is_dir() or path.is_symlink():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _load_manifest_safe(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def get_backup_summary(backup_root: Path) -> dict[str, Any]:
    directories = ensure_backup_layout(backup_root)
    root = Path(backup_root).expanduser().resolve()
    recovery_items = []
    temporary_count = 0
    temporary_bytes = 0
    chapter_archive_count = 0
    chapter_archive_bytes = 0

    for transaction_dir in directories["recovery"].iterdir():
        if not transaction_dir.is_dir() or transaction_dir.is_symlink():
            continue
        manifest = _load_manifest_safe(transaction_dir / "manifest.json") or {}
        backup_file = transaction_dir / str(manifest.get("backup_file") or "original.mkv")
        recovery_items.append({
            "transaction_id": str(manifest.get("transaction_id") or transaction_dir.name),
            "file_name": str(manifest.get("file_name") or backup_file.name),
            "target_path": str(manifest.get("target_path") or ""),
            "episode_id": manifest.get("episode_id"),
            "reason": str(manifest.get("recovery_reason") or "La operación necesita inspección."),
            "backup_bytes": backup_file.stat().st_size if backup_file.is_file() else 0,
        })

    for transaction_dir in directories["temporary"].iterdir():
        if transaction_dir.is_dir() and not transaction_dir.is_symlink():
            temporary_count += 1
            temporary_bytes += _directory_size(transaction_dir)

    for archive_dir in directories["chapters"].iterdir():
        if archive_dir.is_dir() and not archive_dir.is_symlink():
            archive = archive_dir / "original_chapters.xml"
            if archive.is_file():
                chapter_archive_count += 1
                chapter_archive_bytes += archive.stat().st_size

    legacy_backups = [item for item in root.glob("*.backup-*.mkv") if item.is_file() and not item.is_symlink()]
    legacy_bytes = sum(item.stat().st_size for item in legacy_backups)
    disk = shutil.disk_usage(root)
    return {
        "backup_path": str(root),
        "temporary_path": str(directories["temporary"]),
        "recovery_path": str(directories["recovery"]),
        "chapters_path": str(directories["chapters"]),
        "available_free_bytes": disk.free,
        "recovery_count": len(recovery_items),
        "recovery_bytes": sum(item["backup_bytes"] for item in recovery_items),
        "recovery_items": recovery_items,
        "temporary_count": temporary_count,
        "temporary_bytes": temporary_bytes,
        "chapter_archive_count": chapter_archive_count,
        "chapter_archive_bytes": chapter_archive_bytes,
        "legacy_backup_count": len(legacy_backups),
        "legacy_backup_bytes": legacy_bytes,
    }
