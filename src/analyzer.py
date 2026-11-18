# src/analyzer.py
import subprocess
import tempfile
from pathlib import Path
import numpy as np
import librosa
import scipy.signal
from rich.console import Console
from rich.prompt import Confirm

console = Console()
COSINE_SIMILARITY_THRESHOLD = 0.68  # Umbral base de similitud optimizado

def prepare_song_templates(series_template_dir: Path, plugins_path: Path) -> list[Path]:
    video_files_dir = series_template_dir / "video_files"
    songs_files_dir = series_template_dir / "songs_files"
    songs_files_dir.mkdir(parents=True, exist_ok=True)

    existing_wavs = sorted(list(songs_files_dir.glob("*.wav")))
    if existing_wavs:
        return existing_wavs

    video_files = []
    if video_files_dir.exists():
        video_files = [
            f for f in video_files_dir.glob("*.*")
            if f.suffix.lower() in [".mkv", ".mp4", ".ts", ".avi", ".mov", ".webm"]
        ]

    if not video_files:
        return []

    console.print(f"\n[yellow]⚠ No se encontraron archivos .wav en: [bold]{songs_files_dir}[/bold][/yellow]")
    console.print(f"[cyan]Se detectaron [{len(video_files)}] archivos de video en [bold]{video_files_dir}[/bold].[/cyan]")
    
    if not Confirm.ask("¿Deseas procesar los archivos de video para generar las plantillas .wav en songs_files?", default=True):
        console.print("[dim]Conversión omitida por el usuario.[/dim]")
        return []

    ffmpeg_exe = plugins_path / "FFMPEG" / "bin" / "ffmpeg.exe"
    if not ffmpeg_exe.exists():
        console.print(f"[bold red]✘ No se encontró ffmpeg.exe en: {ffmpeg_exe}[/bold red]")
        return []

    console.print("[cyan]⚙ Extrayendo y convirtiendo pistas de audio a formato .wav...[/cyan]")
    for vid in video_files:
        expected_wav = songs_files_dir / f"{vid.stem}.wav"
        if not expected_wav.exists():
            cmd = [
                str(ffmpeg_exe), "-y", "-i", str(vid),
                "-vn", "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                str(expected_wav)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            console.print(f"  [green]✔ Generado:[/green] {expected_wav.name}")

    return sorted(list(songs_files_dir.glob("*.wav")))

def evaluate_episode_templates(episode_path: Path, wav_templates: list, plugins_path: Path, progress_callback) -> tuple[list, list]:
    sr = 22050
    hop_length = 512
    op_candidates = []
    ed_candidates = []

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        ep_wav = temp_dir_path / "episode.wav"

        progress_callback(10)
        ffmpeg_exe = plugins_path / "FFMPEG" / "bin" / "ffmpeg.exe"
        cmd = [
            str(ffmpeg_exe), "-y", "-i", str(episode_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", str(sr), "-ac", "1",
            str(ep_wav)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        progress_callback(30)

        if not ep_wav.exists():
            return [], []

        y_ep, _ = librosa.load(str(ep_wav), sr=sr, mono=True)
        episode_duration = len(y_ep) / sr
        progress_callback(50)

        chroma_ep = librosa.feature.chroma_stft(y=y_ep, sr=sr, hop_length=hop_length)
        chroma_ep = chroma_ep / (np.linalg.norm(chroma_ep, axis=0, keepdims=True) + 1e-6)

        total_templates = len(wav_templates)
        
        for idx, tmpl in enumerate(wav_templates):
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

            match_index = np.argmax(correlation)
            max_score = float(correlation[match_index])

            if max_score >= COSINE_SIMILARITY_THRESHOLD:
                start_time = float(librosa.frames_to_time(match_index, sr=sr, hop_length=hop_length))
                duration = float(len(y_tmpl) / sr)
                end_time = start_time + duration

                is_op = "op" in tmpl.stem.lower()
                is_ed = "ed" in tmpl.stem.lower()

                # REGLA DE SANIDAD TEMPORAL BLINDADA:
                # Un Ending jamás debe empezar antes del último 30% del episodio (evita falsos positivos a mitad de capítulo).
                if is_ed and episode_duration > 0 and start_time < (episode_duration * 0.70):
                    continue

                candidate = {
                    "name": tmpl.stem.upper(),
                    "start": start_time,
                    "end": end_time,
                    "score": max_score
                }
                if is_op:
                    op_candidates.append(candidate)
                elif is_ed:
                    ed_candidates.append(candidate)

            current_progress = 50 + int(((idx + 1) / total_templates) * 45)
            progress_callback(current_progress)

        progress_callback(100)

    return op_candidates, ed_candidates