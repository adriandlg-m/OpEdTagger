from __future__ import annotations

from backend.services.template_service import _recover_template_swap


def test_template_swap_recovers_previous_folder_after_interruption(tmp_path):
    target = tmp_path / "Show"
    previous = tmp_path / ".Show.previous-old"
    staging = tmp_path / ".Show.staging-new"
    previous.mkdir()
    (previous / "template_manifest.json").write_text('{"anime_slug":"show"}', encoding="utf-8")
    staging.mkdir()
    (staging / "partial.bin").write_bytes(b"incomplete")

    _recover_template_swap(target)

    assert target.exists()
    assert (target / "template_manifest.json").exists()
    assert not staging.exists()
    assert not previous.exists()


def test_template_swap_cleans_old_previous_after_new_target_is_committed(tmp_path):
    target = tmp_path / "Show"
    previous = tmp_path / ".Show.previous-old"
    target.mkdir()
    previous.mkdir()
    (target / "template_manifest.json").write_text('{"anime_slug":"new"}', encoding="utf-8")
    (previous / "template_manifest.json").write_text('{"anime_slug":"old"}', encoding="utf-8")

    _recover_template_swap(target)

    assert (target / "template_manifest.json").read_text(encoding="utf-8") == '{"anime_slug":"new"}'
    assert not previous.exists()
