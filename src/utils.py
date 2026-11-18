# src/utils.py
import sys
import re
from pathlib import Path
from datetime import datetime

try:
    from rich.console import Console
except ImportError:
    class Console:
        def print(self, *args, **kwargs):
            print(*args)

from src.mkv_tools import find_mkvpropedit_executable

console = Console()

def validate_environment(cfg, plugins_path, library_path):
    errors = []
    
    template_path = Path(cfg.get("template_path", "template"))
    try:
        template_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        errors.append(f"Failed to create or access template directory ({template_path}): {e}")

    ffmpeg_dir = plugins_path / "FFMPEG" / "bin"
    ffmpeg_exe = ffmpeg_dir / "ffmpeg.exe"
    ffprobe_exe = ffmpeg_dir / "ffprobe.exe"
    
    if not ffmpeg_exe.exists():
        errors.append(f"ffmpeg.exe not found at: {ffmpeg_exe}")
    if not ffprobe_exe.exists():
        errors.append(f"ffprobe.exe not found at: {ffprobe_exe}")
    
    mkvpropedit_exe = find_mkvpropedit_executable(plugins_path)
    if not mkvpropedit_exe or not mkvpropedit_exe.exists():
        errors.append("mkvpropedit.exe not found in the plugins directory.")

    db_dir = Path("database")
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
        test_file = db_dir / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
    except Exception as e:
        errors.append(f"No write permissions in the database/ directory: {e}")

    if library_path.exists():
        try:
            test_lib_file = library_path / ".write_test"
            test_lib_file.write_text("test")
            test_lib_file.unlink()
        except Exception as e:
            errors.append(f"Permission warning/error in the library directory: {e}")

    if errors:
        console.print("[bold red]✘ Critical environment errors detected:[/bold red]")
        for err in errors:
            console.print(f"  • [red]{err}[/red]")
        sys.exit(1)

def write_audit_log(series_name: str, episode_name: str, message: str):
    log_path = Path("audit.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] [{series_name}] [{episode_name}] {message}\n"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        pass

def parse_season_episode(filename: str) -> tuple[int, int] | None:
    patterns = [
        r"[Ss](\d{1,2})[Ee](\d{1,3})",
        r"(\d{1,2})x(\d{1,3})",
        r"[Ss]eason\s*(\d{1,2}).*?[Ee]pisode\s*(\d{1,3})",
    ]
    for pat in patterns:
        match = re.search(pat, filename)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None