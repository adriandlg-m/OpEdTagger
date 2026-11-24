from __future__ import annotations

import wave

import pytest

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



def test_ensure_templates_selects_anime_themes_result_automatically_using_cli_style_matching(tmp_path, monkeypatch):
    from backend.services import template_service

    template_root = tmp_path / "templates"
    monkeypatch.setattr(template_service, "load_config", lambda: {
        "template_path": str(template_root),
        "plugins_path": str(tmp_path / "plugins"),
    })
    seen_queries = []
    selected = []
    candidates = [
        {"slug": "unrelated-anime", "title_romaji": "Unrelated Anime", "opening_count": 2, "ending_count": 1},
        {"slug": "my-show-season-2", "title_romaji": "My Show Season 2", "opening_count": 3, "ending_count": 2},
    ]
    monkeypatch.setattr(
        template_service,
        "search_candidates",
        lambda query: (seen_queries.append(query) or candidates),
    )
    monkeypatch.setattr(
        template_service,
        "download_selected_templates",
        lambda series_name, slug, progress=None: selected.append((series_name, slug)),
    )

    result = template_service.ensure_templates("My Show (2022) [1080p]")

    assert result == template_root / "My Show (2022) [1080p]"
    assert seen_queries == ["My Show"]
    assert selected == [("My Show (2022) [1080p]", "my-show-season-2")]


def test_automatic_template_selection_falls_back_to_first_result_with_op_ed():
    from backend.services.template_service import _choose_automatic_candidate

    candidates = [
        {"slug": "anime-without-themes", "opening_count": 0, "ending_count": 0},
        {"slug": "provider-ranked-match", "opening_count": 1, "ending_count": 1},
    ]

    selected = _choose_automatic_candidate("Local Folder Name", candidates)

    assert selected["slug"] == "provider-ranked-match"



def test_wav_validation_excludes_corrupt_files_before_analysis(tmp_path):
    from backend.services.analysis_service import _validate_wav_templates

    valid = tmp_path / "valid.wav"
    with wave.open(str(valid), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(b"\\x00\\x00" * 220)

    invalid = tmp_path / "broken.wav"
    invalid.write_bytes(b"not a wave file")

    assert _validate_wav_templates([valid, invalid], "Example Show") == [valid]
    with pytest.raises(RuntimeError, match="No valid WAV templates"):
        _validate_wav_templates([invalid], "Example Show")


def test_template_download_creates_both_folders_and_preserves_existing_song_files(tmp_path, monkeypatch):
    from backend.services import template_service

    template_root = tmp_path / "templates"
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
