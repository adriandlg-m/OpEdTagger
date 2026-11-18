# src/setup_plugins.py
import os
import sys
import shutil
import urllib.request
import zipfile
import py7zr
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
from rich.panel import Panel
from rich.text import Text

console = Console()

PLUGINS_DIR = Path("plugins")
FFMPEG_BIN_DIR = PLUGINS_DIR / "FFMPeg" / "bin"

FPCALC_URL = "https://github.com/acoustid/chromaprint/releases/download/v1.5.1/chromaprint-fpcalc-1.5.1-windows-x86_64.zip"
MKVTOOLNIX_URL = "https://mkvtoolnix.download/windows/releases/82.0/mkvtoolnix-64-bit-82.0.7z"
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

def download_with_progress(url: str, dest_path: Path, description: str):
    PLUGINS_DIR.mkdir(exist_ok=True)
    
    with urllib.request.urlopen(url) as response:
        total_size = int(response.info().get("Content-Length", 0))
        
        with Progress(
            SpinnerColumn("dots", style="cyan"),
            TextColumn(f"[bold cyan]{description}[/]", justify="right"),
            BarColumn(bar_width=30, complete_style="green", finished_style="bold green"),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            task = progress.add_task("download", total=total_size)
            
            chunk_size = 8192
            with open(dest_path, "wb") as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))

def setup_plugins():
    console.print(Panel.fit(
        Text("OpEdTagger • Environment & Plugin Initializer", justify="center", style="bold magenta"),
        border_style="cyan"
    ))
    
    PLUGINS_DIR.mkdir(exist_ok=True)
    FFMPEG_BIN_DIR.mkdir(parents=True, exist_ok=True)

    fpcalc_path = PLUGINS_DIR / "fpcalc.exe"
    mkv_tools = {"mkvmerge.exe", "mkvextract.exe", "mkvinfo.exe", "mkvpropedit.exe"}
    missing_mkv = [t for t in mkv_tools if not (PLUGINS_DIR / t).exists()]
    ffmpeg_binaries = {"ffmpeg.exe", "ffprobe.exe", "ffplay.exe"}
    missing_ffmpeg = [b for b in ffmpeg_binaries if not (FFMPEG_BIN_DIR / b).exists()]

    if fpcalc_path.exists() and not missing_mkv and not missing_ffmpeg:
        console.print("[green]✔[/green] [bold]All binary dependencies and plugins are correctly installed![/bold]\n")
        return

    console.print("[yellow]⚡ Some required binary tools are missing. Preparing automatic setup...[/yellow]\n")

    if not fpcalc_path.exists():
        zip_path = PLUGINS_DIR / "fpcalc.zip"
        download_with_progress(FPCALC_URL, zip_path, "Downloading fpcalc (Chromaprint)...")
        
        console.print("[cyan]⚙[/cyan] Extracting [bold]fpcalc.exe[/bold]...")
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(PLUGINS_DIR / "temp_fpcalc")
        for root, _, files in os.walk(PLUGINS_DIR / "temp_fpcalc"):
            if "fpcalc.exe" in files:
                shutil.move(os.path.join(root, "fpcalc.exe"), fpcalc_path)
        shutil.rmtree(PLUGINS_DIR / "temp_fpcalc", ignore_errors=True)
        if zip_path.exists():
            zip_path.unlink()
        console.print("[green]  -> fpcalc.exe installed successfully.[/green]\n")

    if missing_mkv:
        archive_path = PLUGINS_DIR / "mkvtoolnix.7z"
        download_with_progress(MKVTOOLNIX_URL, archive_path, "Downloading MKVToolNix suite...")
        
        console.print("[cyan]⚙[/cyan] Extracting MKV tools ([magenta].7z[/magenta])...")
        with py7zr.SevenZipFile(archive_path, mode='r') as z:
            z.extractall(PLUGINS_DIR / "temp_mkv")
            
        for root, _, files in os.walk(PLUGINS_DIR / "temp_mkv"):
            for f in files:
                if f in mkv_tools:
                    shutil.move(os.path.join(root, f), PLUGINS_DIR / f)
                    
        shutil.rmtree(PLUGINS_DIR / "temp_mkv", ignore_errors=True)
        if archive_path.exists():
            archive_path.unlink()
        console.print("[green]  -> MKVToolNix tools installed successfully.[/green]\033[0m\n")

    if missing_ffmpeg:
        ffmpeg_zip = PLUGINS_DIR / "ffmpeg.zip"
        download_with_progress(FFMPEG_URL, ffmpeg_zip, "Downloading FFmpeg binaries...")
        
        console.print("[cyan]⚙[/cyan] Extracting FFmpeg core binaries...")
        with zipfile.ZipFile(ffmpeg_zip, 'r') as z:
            z.extractall(PLUGINS_DIR / "temp_ffmpeg")
            
        for root, _, files in os.walk(PLUGINS_DIR / "temp_ffmpeg"):
            for f in files:
                if f in ffmpeg_binaries:
                    shutil.move(os.path.join(root, f), FFMPEG_BIN_DIR / f)
                    
        shutil.rmtree(PLUGINS_DIR / "temp_ffmpeg", ignore_errors=True)
        if ffmpeg_zip.exists():
            ffmpeg_zip.unlink()
        console.print("[green]  -> FFmpeg binaries installed successfully.[/green]\n")

    console.print(Panel("[bold green]✔ Environment check complete! All systems operational.[/bold green]", border_style="green"))

if __name__ == "__main__":
    setup_plugins()