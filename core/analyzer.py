# src/analyzer.py
import json
import logging
import subprocess
import tempfile
import shutil
import uuid
from pathlib import Path
from typing import Callable

import numpy as np
import librosa
import scipy.signal

from core.fingerprints import file_fingerprint

logger = logging.getLogger(__name__)


def _binary(plugins_path: Path, name: str) -> Path:
    candidates = [
        plugins_path / "FFMPEG" / "bin" / f"{name}.exe",
        plugins_path / "FFmpeg" / "bin" / f"{name}.exe",
        plugins_path / "ffmpeg" / "bin" / f"{name}.exe",
        plugins_path / "FFMPEG" / "bin" / name,
        plugins_path / "FFmpeg" / "bin" / name,
        plugins_path / "ffmpeg" / "bin" / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    system = shutil.which(name)
    if system:
        return Path(system)
    raise FileNotFoundError(f"{name} was not found. Configure plugins_path or add it to PATH.")


COSINE_SIMILARITY_THRESHOLD = 0.68


def _peak_width_seconds(
    correlation: np.ndarray,
    peak_index: int,
    sr: int,
    hop_length: int,
    relative_level: float = 0.95,
) -> float | None:
    """Measure how wide the contiguous top of a correlation peak is.

    Peaks touching the search boundary are censored and cannot provide a
    reliable width estimate.
    """
    values = np.asarray(correlation, dtype=float)
    if values.ndim != 1 or peak_index < 0 or peak_index >= len(values):
        return None
    peak_value = float(values[peak_index])
    if not np.isfinite(peak_value) or peak_value <= 0:
        return None
    if not 0 < relative_level < 1 or sr <= 0 or hop_length <= 0:
        return None

    threshold = peak_value * relative_level
    left = peak_index
    right = peak_index
    while left > 0 and values[left - 1] >= threshold:
        left -= 1
    while right < len(values) - 1 and values[right + 1] >= threshold:
        right += 1

    if left == 0 or right == len(values) - 1:
        return None
    return float((right - left + 1) * hop_length / sr)


def prepare_song_templates(
    series_template_dir: Path,
    plugins_path: Path,
    on_template_progress: Callable[[int, int, str], None] | None = None,
) -> list[Path]:
    """Convert templates to WAV and invalidate derived audio when source videos change."""
    video_files_dir = series_template_dir / "video_files"
    songs_files_dir = series_template_dir / "songs_files"
    songs_files_dir.mkdir(parents=True, exist_ok=True)

    video_files: list[Path] = []
    if video_files_dir.exists():
        video_files = sorted(
            (f for f in video_files_dir.glob("*.*")
             if f.suffix.lower() in [".mkv", ".mp4", ".ts", ".avi", ".mov", ".webm"]),
            key=lambda item: item.name.lower(),
        )

    manifest_path = songs_files_dir / ".template_wav_manifest.json"
    try:
        old_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        if not isinstance(old_manifest, dict):
            old_manifest = {}
    except (OSError, ValueError):
        old_manifest = {}

    # If only manually supplied WAVs remain, preserve them. Remove only outputs
    # that this manifest generated from source videos that have since disappeared.
    if not video_files:
        for entry in old_manifest.values():
            if not isinstance(entry, dict):
                continue
            old_wav = songs_files_dir / str(entry.get("wav_file") or "")
            try:
                old_wav.resolve().relative_to(songs_files_dir.resolve())
                if (old_wav.suffix.lower() == ".wav" and old_wav.is_file()
                        and entry.get("wav_fingerprint") == file_fingerprint(old_wav)):
                    old_wav.unlink()
            except (ValueError, OSError, RuntimeError):
                continue
        if manifest_path.exists():
            manifest_path.unlink()
        return sorted(songs_files_dir.glob("*.wav"), key=lambda item: item.name.lower())

    ffmpeg_exe = _binary(plugins_path, "ffmpeg")
    new_manifest: dict[str, dict[str, str]] = {}
    current_sources = {video.name for video in video_files}
    total = len(video_files)

    for file_idx, vid in enumerate(video_files):
        if callable(on_template_progress):
            on_template_progress(file_idx, total, vid.stem)
        expected_wav = songs_files_dir / f"{vid.stem}.wav"
        source_fingerprint = file_fingerprint(vid)
        previous = old_manifest.get(vid.name) if isinstance(old_manifest.get(vid.name), dict) else {}
        wav_fingerprint = None
        if expected_wav.exists() and previous.get("source_fingerprint") == source_fingerprint:
            try:
                wav_fingerprint = file_fingerprint(expected_wav)
            except (OSError, RuntimeError):
                wav_fingerprint = None
        if wav_fingerprint and wav_fingerprint == previous.get("wav_fingerprint"):
            new_manifest[vid.name] = {
                "wav_file": expected_wav.name,
                "source_fingerprint": source_fingerprint,
                "wav_fingerprint": wav_fingerprint,
            }
            continue

        temp_wav = songs_files_dir / f".{vid.stem}.{uuid.uuid4().hex[:8]}.tmp.wav"
        cmd = [
            str(ffmpeg_exe), "-y", "-i", str(vid),
            "-vn", "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
            str(temp_wav),
        ]
        try:
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    text=True, timeout=600)
            if result.returncode != 0 or not temp_wav.exists() or temp_wav.stat().st_size <= 44:
                raise RuntimeError((result.stderr or "empty WAV output")[-500:])
            temp_wav.replace(expected_wav)
            wav_fingerprint = file_fingerprint(expected_wav)
            new_manifest[vid.name] = {
                "wav_file": expected_wav.name,
                "source_fingerprint": source_fingerprint,
                "wav_fingerprint": wav_fingerprint,
            }
            logger.info("Generated WAV template: %s", expected_wav.name)
        except Exception as exc:
            # Never leave a WAV from an older source masquerading as the new template.
            expected_wav.unlink(missing_ok=True)
            logger.warning("ffmpeg failed for %s: %s", vid.name, str(exc)[-500:])
        finally:
            temp_wav.unlink(missing_ok=True)

    # Remove only WAV files that this manifest previously generated for deleted videos.
    for source_name, entry in old_manifest.items():
        if source_name in current_sources or not isinstance(entry, dict):
            continue
        old_wav = songs_files_dir / str(entry.get("wav_file") or "")
        try:
            old_wav.resolve().relative_to(songs_files_dir.resolve())
            if old_wav.suffix.lower() == ".wav" and old_wav.is_file():
                if entry.get("wav_fingerprint") == file_fingerprint(old_wav):
                    old_wav.unlink()
        except (ValueError, OSError, RuntimeError):
            continue

    manifest_tmp = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex[:8]}.tmp")
    manifest_tmp.write_text(json.dumps(new_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest_tmp.replace(manifest_path)
    if callable(on_template_progress):
        on_template_progress(total, total, "done")

    return sorted(songs_files_dir.glob("*.wav"), key=lambda item: item.name.lower())

def evaluate_episode_templates(episode_path: Path, wav_templates: list, plugins_path: Path,
                               progress_callback=None, cancel_event=None) -> tuple[list, list]:
    if progress_callback is None:
        progress_callback = lambda _pct: None
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Analysis cancellation requested.")
    sr = 22050
    hop_length = 512
    op_candidates = []
    ed_candidates = []

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        ep_wav = temp_dir_path / "episode.wav"

        progress_callback(10)
        ffmpeg_exe = _binary(plugins_path, "ffmpeg")
        cmd = [
            str(ffmpeg_exe), "-y", "-i", str(episode_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", str(sr), "-ac", "1",
            str(ep_wav)
        ]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=1800)
        progress_callback(30)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Analysis cancellation requested.")

        if result.returncode != 0 or not ep_wav.exists() or ep_wav.stat().st_size <= 44:
            raise RuntimeError(f"FFmpeg could not extract audio from '{episode_path.name}': {(result.stderr or '')[-500:]}")

        y_ep, _ = librosa.load(str(ep_wav), sr=sr, mono=True)
        episode_duration = len(y_ep) / sr
        progress_callback(50)

        chroma_ep = librosa.feature.chroma_stft(y=y_ep, sr=sr, hop_length=hop_length)
        chroma_ep = chroma_ep / (np.linalg.norm(chroma_ep, axis=0, keepdims=True) + 1e-6)

        total_templates = len(wav_templates)
        
        for idx, tmpl in enumerate(wav_templates):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Analysis cancellation requested.")
            if not tmpl.exists():
                continue

            y_tmpl, _ = librosa.load(str(tmpl), sr=sr, mono=True)
            if len(y_ep) < len(y_tmpl):
                continue

            y_tmpl_trimmed, _ = librosa.effects.trim(y_tmpl, top_db=25)
            if len(y_tmpl_trimmed) > sr * 5:
                y_tmpl = y_tmpl_trimmed

            chroma_tmpl = librosa.feature.chroma_stft(y=y_tmpl, sr=sr, hop_length=hop_length)
            if chroma_ep.shape[1] < chroma_tmpl.shape[1]:
                continue

            chroma_tmpl = chroma_tmpl / (np.linalg.norm(chroma_tmpl, axis=0, keepdims=True) + 1e-6)

            correlation = np.zeros(chroma_ep.shape[1] - chroma_tmpl.shape[1] + 1)
            for i in range(chroma_ep.shape[0]):
                correlation += scipy.signal.correlate(chroma_ep[i], chroma_tmpl[i], mode="valid")
            
            correlation /= chroma_ep.shape[0]

            # Keep the original detector score and matching decision unchanged.
            # This normalized metric is additional evidence for the confidence
            # model; it is not used to select or reject a detected segment.
            match_index = np.argmax(correlation)
            max_score = float(correlation[match_index])
            template_frames = int(chroma_tmpl.shape[1])
            normalized_correlation = np.clip(
                correlation * (chroma_ep.shape[0] / max(template_frames, 1)),
                0.0,
                1.0,
            )
            similarity_score = float(normalized_correlation[match_index])

            # Compare the best peak with the strongest peak outside a two-second
            # neighborhood. Adjacent frames belong to the same detection peak.
            exclusion_radius = max(1, int((2.0 * sr) / hop_length))
            separated_peaks = normalized_correlation.copy()
            left = max(0, match_index - exclusion_radius)
            right = min(len(separated_peaks), match_index + exclusion_radius + 1)
            separated_peaks[left:right] = -np.inf
            second_peak = float(np.max(separated_peaks)) if len(separated_peaks) else float("-inf")
            peak_prominence = (
                float(np.clip(
                    (similarity_score - second_peak) / max(abs(similarity_score), 1e-8),
                    0.0,
                    1.0,
                ))
                if np.isfinite(second_peak)
                else None
            )
            peak_width_seconds = _peak_width_seconds(
                normalized_correlation, match_index, sr, hop_length
            )

            if max_score >= COSINE_SIMILARITY_THRESHOLD:
                start_time = float(librosa.frames_to_time(match_index, sr=sr, hop_length=hop_length))
                duration = float(len(y_tmpl) / sr)
                end_time = start_time + duration

                is_op = "op" in tmpl.stem.lower()
                is_ed = "ed" in tmpl.stem.lower()

                if is_ed and episode_duration > 0 and start_time < (episode_duration * 0.70):
                    continue

                candidate = {
                    "name": tmpl.stem.upper(),
                    "start": start_time,
                    "end": end_time,
                    "score": max_score,
                    "similarity_score": similarity_score,
                    "peak_prominence": peak_prominence,
                    "peak_width_seconds": peak_width_seconds,
                    "template_frames": template_frames,
                }
                if is_op:
                    op_candidates.append(candidate)
                elif is_ed:
                    ed_candidates.append(candidate)

            current_progress = 50 + int(((idx + 1) / total_templates) * 45)
            progress_callback(current_progress)

        progress_callback(100)

    return op_candidates, ed_candidates