from __future__ import annotations

import wave
from pathlib import Path

import pytest

from backend.services import analysis_service


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22050)
        audio.writeframes(b"\x00\x00" * 220)


def test_get_templates_checks_plugins_then_syncs_and_prepares_wavs(tmp_path, monkeypatch):
    template_dir = tmp_path / "templates" / "Example Show"
    template_dir.mkdir(parents=True)
    wav_path = template_dir / "songs_files" / "OP1.wav"
    wav_path.parent.mkdir()
    _make_wav(wav_path)

    calls = []
    monkeypatch.setattr(
        analysis_service,
        "load_config",
        lambda: {"plugins_path": str(tmp_path / "plugins")},
    )
    monkeypatch.setattr(
        analysis_service,
        "ensure_analysis_plugins",
        lambda _cfg: (calls.append("plugins") or {"status": "analysis_ready"}),
    )
    monkeypatch.setattr(
        analysis_service,
        "ensure_templates",
        lambda name, slug, progress: (calls.append("sync_templates") or template_dir),
    )
    monkeypatch.setattr(
        analysis_service,
        "prepare_song_templates",
        lambda directory, plugins, on_template_progress=None: (
            calls.append("prepare_wavs") or [wav_path]
        ),
    )
    monkeypatch.setattr(analysis_service, "templates_signature", lambda _wavs: "signature")

    wavs, signature = analysis_service._get_templates("Example Show", None)

    assert calls == ["plugins", "sync_templates", "prepare_wavs"]
    assert wavs == [wav_path]
    assert signature == "signature"


def test_get_templates_stops_before_download_when_ffmpeg_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        analysis_service,
        "load_config",
        lambda: {"plugins_path": str(tmp_path / "plugins")},
    )
    monkeypatch.setattr(
        analysis_service,
        "ensure_analysis_plugins",
        lambda _cfg: {"status": "error", "last_error": "FFmpeg could not be installed."},
    )
    monkeypatch.setattr(
        analysis_service,
        "ensure_templates",
        lambda *_args, **_kwargs: pytest.fail("Templates should not download without FFmpeg."),
    )

    with pytest.raises(RuntimeError, match="FFmpeg could not be installed"):
        analysis_service._get_templates("Example Show", None)
