from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core import backup_manager, media_tools


def test_sequential_estimate_uses_largest_file_not_sum_of_all_mkvs(tmp_path, monkeypatch):
    first = tmp_path / "first.mkv"
    second = tmp_path / "second.mkv"
    first.write_bytes(b"A" * 1024)
    second.write_bytes(b"B" * 2048)
    monkeypatch.setattr(
        backup_manager.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10_000_000_000, used=1, free=9_000_000_000),
    )

    estimate = backup_manager.estimate_sequential_backup_space(
        [first, second], tmp_path / "backups"
    )

    assert estimate["selected_bytes"] == 3072
    assert estimate["largest_file"] == "second.mkv"
    assert estimate["largest_file_bytes"] == 2048
    assert estimate["required_free_bytes"] == 2048 + backup_manager.MINIMUM_FREE_RESERVE_BYTES
    assert estimate["enough_space"] is True


def _write_transaction(root: Path, transaction_id: str, phase: str, episode_id: int) -> Path:
    layout = backup_manager.ensure_backup_layout(root)
    transaction = layout["temporary"] / transaction_id
    transaction.mkdir()
    (transaction / "original.mkv").write_bytes(b"original")
    backup_manager.write_transaction_manifest(transaction, {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "episode_id": episode_id,
        "file_name": f"S01E{episode_id:02d}.mkv",
        "target_path": str(root / f"S01E{episode_id:02d}.mkv"),
        "backup_file": "original.mkv",
        "original_size": 8,
        "phase": phase,
    })
    return transaction


def test_reconcile_removes_transactions_that_never_modified_the_mkv_and_resets_state(tmp_path):
    temporary = _write_transaction(tmp_path / "backups", "no-write", "BACKUP_READY", 1)
    states = {}

    result = media_tools.reconcile_backup_transactions(
        tmp_path / "backups",
        lambda _episode_id: "RECOVERY_REQUIRED",
        lambda episode_id, status, error: states.update({episode_id: (status, error)}),
    )

    assert result["removed_incomplete"] == 1
    assert not temporary.exists()
    assert states[1] == ("NOT_REQUESTED", None)


def test_reconcile_moves_interrupted_write_to_recovery_and_marks_episode(tmp_path):
    temporary = _write_transaction(tmp_path / "backups", "interrupted", "INJECTING", 2)
    states = {}

    result = media_tools.reconcile_backup_transactions(
        tmp_path / "backups",
        lambda _episode_id: "RECOVERY_REQUIRED",
        lambda episode_id, status, error: states.update({episode_id: (status, error)}),
    )

    assert result["moved_to_recovery"] == 1
    assert not temporary.exists()
    assert states[2][0] == "RECOVERY_REQUIRED"
    recovery_dirs = list((tmp_path / "backups" / "recovery").iterdir())
    assert len(recovery_dirs) == 1
    assert (recovery_dirs[0] / "original.mkv").read_bytes() == b"original"


def test_reconcile_finalizes_verified_transaction_only_when_database_succeeded(tmp_path):
    temporary = _write_transaction(tmp_path / "backups", "verified", "VERIFIED", 3)
    states = {}

    result = media_tools.reconcile_backup_transactions(
        tmp_path / "backups",
        lambda _episode_id: "SUCCEEDED",
        lambda episode_id, status, error: states.update({episode_id: (status, error)}),
    )

    assert result["finalized"] == 1
    assert not temporary.exists()
    assert list((tmp_path / "backups" / "temporary").iterdir()) == []
    assert states == {}



def test_restore_recovery_backup_verifies_original_and_updates_episode_before_cleanup(tmp_path):
    from core.fingerprints import file_fingerprint

    root = tmp_path / "project" / "backups"
    library = tmp_path / "library"
    library.mkdir(parents=True)
    target = library / "episode.mkv"
    target.write_bytes(b"partially modified content")
    layout = backup_manager.ensure_backup_layout(root)
    recovery = layout["recovery"] / "txn-restore"
    recovery.mkdir()
    backup = recovery / "original.mkv"
    backup.write_bytes(b"original full mkv content")
    fingerprint = file_fingerprint(backup)
    backup_manager.write_transaction_manifest(recovery, {
        "transaction_id": "txn-restore",
        "episode_id": 12,
        "file_name": target.name,
        "target_path": str(target),
        "backup_file": "original.mkv",
        "original_size": backup.stat().st_size,
        "original_fingerprint": fingerprint,
        "phase": "RECOVERY_REQUIRED",
    })
    updated = {}

    result = backup_manager.restore_recovery_backup(
        "txn-restore",
        root,
        library,
        lambda episode_id, path: updated.update({episode_id: str(path)}),
    )

    assert target.read_bytes() == b"original full mkv content"
    assert result["restored"] is True
    assert updated == {12: str(target)}
    assert not recovery.exists()


def test_restore_recovery_backup_keeps_backup_when_episode_update_fails(tmp_path):
    from core.fingerprints import file_fingerprint
    import pytest

    root = tmp_path / "backups"
    library = tmp_path / "library"
    library.mkdir(parents=True)
    target = library / "episode.mkv"
    target.write_bytes(b"partial")
    layout = backup_manager.ensure_backup_layout(root)
    recovery = layout["recovery"] / "txn-db-fail"
    recovery.mkdir()
    backup = recovery / "original.mkv"
    backup.write_bytes(b"original")
    backup_manager.write_transaction_manifest(recovery, {
        "transaction_id": "txn-db-fail",
        "episode_id": 13,
        "file_name": target.name,
        "target_path": str(target),
        "backup_file": "original.mkv",
        "original_size": backup.stat().st_size,
        "original_fingerprint": file_fingerprint(backup),
        "phase": "RECOVERY_REQUIRED",
    })

    def fail_db_update(_episode_id, _path):
        raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        backup_manager.restore_recovery_backup("txn-db-fail", root, library, fail_db_update)

    assert target.read_bytes() == b"original"
    assert recovery.is_dir()
    assert (recovery / "original.mkv").read_bytes() == b"original"



def test_reconcile_retries_database_committed_cleanup_after_interruption(tmp_path):
    temporary = _write_transaction(
        tmp_path / "backups", "db-committed", "DATABASE_COMMITTED", 4
    )
    states = {}

    result = media_tools.reconcile_backup_transactions(
        tmp_path / "backups",
        lambda _episode_id: "SUCCEEDED",
        lambda episode_id, status, error: states.update({episode_id: (status, error)}),
    )

    assert result["finalized"] == 1
    assert not temporary.exists()
    assert list((tmp_path / "backups" / "temporary").iterdir()) == []
    assert states == {}
