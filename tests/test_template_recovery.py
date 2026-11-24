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


def test_template_download_creates_both_folders_and_preserves_existing_song_files(tmp_path, monkeypatch):
    from backend.services import template_service

    template_root = tmp_path / "Templates"
    plugins_root = tmp_path / "plugins"
    existing_songs = template_root / "Example Show" / "songs_files"
    existing_songs.mkdir(parents=True)
    (existing_songs / "manual-reference.wav").write_bytes(b"manual-wav")
    (existing_songs / ".template_wav_manifest.json").write_text('{"old source": {}}', encoding="utf-8")

    monkeypatch.setattr(template_service, "load_config", lambda: {
        "template_path": str(template_root),
        "plugins_path": str(plugins_root),
    })
    monkeypatch.setattr(template_service, "search_candidates", lambda _query: [{"slug": "example-show"}])
    monkeypatch.setattr(template_service, "_post_graphql", lambda _query: {
        "data": {"search": {"anime": {"data": [{
            "slug": "example-show",
            "title": {"romaji": "Example Show", "english": "Example Show"},
            "themes": [{
                "type": "OP",
                "sequence": 1,
                "entries": [{"videos": {"nodes": [{
                    "link": "https://cdn.animethemes.moe/example-op.webm",
                    "filename": "example-op.webm",
                    "resolution": 1080,
                }]}}],
            }],
        }]}}}
    })

    def fake_download(_url, target):
        target.write_bytes(b"downloaded-video")

    monkeypatch.setattr(template_service, "_download", fake_download)

    def fake_prepare(template_dir, _plugins_path, on_template_progress=None):
        wav_dir = template_dir / "songs_files"
        generated = wav_dir / "Example Show - OP1.wav"
        generated.write_bytes(b"generated-wav")
        (wav_dir / ".template_wav_manifest.json").write_text(
            '{"Example Show - OP1.webm": {}}',
            encoding="utf-8",
        )
        if on_template_progress:
            on_template_progress(1, 1, generated.name)
        return sorted(wav_dir.glob("*.wav"))

    monkeypatch.setattr(template_service, "prepare_song_templates", fake_prepare)

    result = template_service.download_selected_templates("Example Show", "example-show")
    series_dir = template_root / "Example Show"

    assert result["downloaded"] == 1
    assert result["song_templates"] == 2
    assert result["audio_preparation_error"] is None
    assert result["audio_preparation_warning"] is None
    assert (series_dir / "video_files" / "Example Show - OP1.webm").read_bytes() == b"downloaded-video"
    assert (series_dir / "songs_files" / "manual-reference.wav").read_bytes() == b"manual-wav"
    assert (series_dir / "songs_files" / "Example Show - OP1.wav").read_bytes() == b"generated-wav"
    assert (series_dir / "songs_files" / ".template_wav_manifest.json").exists()
