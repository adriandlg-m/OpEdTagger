# main.py
import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from src.config import load_config
from src.database import load_master_db
from src.analyzer import prepare_song_templates
from src.downloader import sync_anime_templates
from src.utils import validate_environment
from src.processor import process_series_pipeline
from src.setup_plugins import setup_plugins

console = Console()

def main():
    console.print(Panel.fit("[bold cyan]🎯 OpEdTagger - Timestamp Audit & Injection[/bold cyan]", border_style="cyan"))
    
    cfg = load_config()
    library_path = Path(cfg["library_path"])
    template_base = Path(cfg["template_path"])
    plugins_path = Path(cfg["plugins_path"])

    setup_plugins()
    validate_environment(cfg, plugins_path, library_path)

    if not library_path.exists():
        console.print(f"[bold red]✘ Library directory does not exist:[/bold red] {library_path}")
        sys.exit(1)

    while True:
        master_db = load_master_db()
        library_series = sorted([d for d in library_path.iterdir() if d.is_dir()])
        if not library_series:
            console.print("[yellow]No series directories found in the library.[/yellow]")
            return

        series_options = []
        for idx, series_dir in enumerate(library_series, start=1):
            series_name = series_dir.name
            template_series_dir = template_base / series_name
            video_files_dir = template_series_dir / "video_files"
            
            has_template = template_series_dir.exists()
            has_videos = False
            if has_template and video_files_dir.exists():
                videos = [f for f in video_files_dir.glob("*.*") if f.suffix.lower() in [".mkv", ".mp4", ".ts", ".avi", ".mov", ".webm"]]
                has_videos = len(videos) > 0

            is_ready = has_template and has_videos

            mkv_files = sorted(list(series_dir.rglob("*.mkv")))
            db_series_data = master_db.get(series_name, {})
            has_mkv = len(mkv_files) > 0

            if not has_mkv:
                status_desc = "[yellow]⚠ No .mkv files[/yellow]"
            elif not is_ready:
                status_desc = "[yellow]⚡ Template pending (API)[/yellow]"
            elif not db_series_data:
                status_desc = "[yellow]⏳ Unprocessed[/yellow]"
            else:
                unprocessed_count = sum(1 for mkv in mkv_files if mkv.name not in db_series_data)
                if unprocessed_count > 0:
                    status_desc = f"[yellow]⚡ {unprocessed_count} new pending[/yellow]"
                else:
                    status_desc = "[green]✔ Up to date[/green]"

            series_options.append({
                "index": idx,
                "name": series_name,
                "dir": series_dir,
                "template_dir": template_series_dir,
                "has_mkv": has_mkv,
                "status": status_desc
            })

        table = Table(title="Available Series Library", border_style="cyan")
        table.add_column("No.", justify="center", style="bold cyan", width=4)
        table.add_column("Series Name", style="bold white")
        table.add_column("Status", justify="center")

        for opt in series_options:
            table.add_row(str(opt["index"]), opt["name"], opt["status"])

        console.print(table)
        console.print("[dim]Select the series number to analyze, or press Enter to exit.[/dim]")

        selected_series = None
        while True:
            choice = Prompt.ask("Enter series number", default="")
            if not choice.strip():
                console.print("[yellow]Goodbye![/yellow]")
                return

            if not choice.isdigit():
                console.print("[bold red]✘ Please enter a valid number.[/bold red]")
                continue

            selected_idx = int(choice)
            selected_series = next((s for s in series_options if s["index"] == selected_idx), None)

            if not selected_series:
                console.print(f"[bold red]✘ Number {selected_idx} is not in the list.[/bold red]")
                continue

            if not selected_series["has_mkv"]:
                console.print(f"[bold red]✘ Series '{selected_series['name']}' has no .mkv episodes.[/bold red]\n")
                continue

            break

        series_name = selected_series["name"]
        series_dir = selected_series["template_dir"]
        target_lib_folder = selected_series["dir"]

        console.rule(f"[bold cyan]Selected Series: {series_name}[/bold cyan]")

        console.print(f"[cyan]🔄 Verifying and syncing templates for '{series_name}'...[/cyan]")
        sync_anime_templates(series_name, template_base)

        wav_templates = prepare_song_templates(series_dir, plugins_path)
        if not wav_templates:
            console.print(f"[yellow]No .wav templates generated for {series_name}.[/yellow]\n")
            continue

        mkv_files = sorted(list(target_lib_folder.rglob("*.mkv")))
        if not mkv_files:
            console.print(f"[yellow]No .mkv episodes found in {target_lib_folder}[/yellow]\n")
            continue

        if not Confirm.ask(f"Do you want to process [{len(mkv_files)}] episodes for [bold]{target_lib_folder.name}[/bold]?", default=False):
            console.print("[dim]Process skipped for this series.[/dim]\n")
            continue

        process_series_pipeline(series_name, target_lib_folder, mkv_files, wav_templates, master_db, plugins_path)

        console.print()
        console.print(Panel.fit(f"[bold green]✔ Process for {series_name} completed successfully![/bold green]", border_style="green"))
        console.print("\n[cyan]Returning to series selection menu...[/cyan]\n")

if __name__ == "__main__":
    main()