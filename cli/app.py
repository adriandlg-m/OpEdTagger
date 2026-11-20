# cli/app.py
import sys
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table

from cli.ui import extract_short_tag, format_compact_timestamp, generate_audit_table
from src.analyzer import prepare_song_templates
from src.config import load_config
from src.database import load_master_db
from src.downloader import sync_anime_templates
from src.processor import process_series_pipeline
from src.setup_plugins import setup_plugins
from src.utils import parse_season_episode, validate_environment

console = Console()


def _format_segment(seg: dict | None) -> str | None:
    if not seg:
        return None
    start_str = format_compact_timestamp(seg["start"])
    end_str = format_compact_timestamp(seg["end"])
    return f"{start_str} - {end_str} [{seg['score']:.2f}]"


def _build_row_from_result(item: dict) -> dict:
    op_data = item.get("op")
    ed_data = item.get("ed")

    op_tag = extract_short_tag(op_data["name"]) if op_data else None
    ed_tag = extract_short_tag(ed_data["name"]) if ed_data else None

    if item.get("source") == "cache":
        if op_tag and ed_tag:
            analysis_status = f"[cyan]⚡ Cache: {op_tag} + {ed_tag}[/cyan]"
        elif op_tag:
            analysis_status = f"[cyan]⚡ Cache: Only {op_tag}[/cyan]"
        elif ed_tag:
            analysis_status = f"[cyan]⚡ Cache: Only {ed_tag}[/cyan]"
        else:
            analysis_status = "[cyan]⚡ Cache: No match[/cyan]"
        db_status_text = "[blue]✔ Cache[/blue]"
    else:
        if op_tag and ed_tag:
            analysis_status = f"[green]✔ {op_tag} + {ed_tag}[/green]"
        elif op_tag:
            analysis_status = f"[yellow]⚠ Only {op_tag}[/yellow]"
        elif ed_tag:
            analysis_status = f"[yellow]⚠ Only {ed_tag}[/yellow]"
        else:
            analysis_status = "[red]✘ No match[/red]"
        db_status_text = "[green]✔ Saved[/green]"

    applied_map = {
        "applied": "[green]✔ Applied[/green]",
        "error": "[red]✘ Error[/red]",
        "mkv_error": "[red]✘ MKV Error[/red]",
        "na": "[dim]N/A[/dim]",
    }
    applied_status = applied_map.get(item.get("applied_status"), "[dim]Pending[/dim]")

    return {
        "file": item["display_name"],
        "analisis": analysis_status,
        "op_text": _format_segment(op_data),
        "ed_text": _format_segment(ed_data),
        "is_anomaly_op": item.get("is_anomaly_op", False),
        "is_anomaly_ed": item.get("is_anomaly_ed", False),
        "db_status": db_status_text,
        "applied": applied_status,
    }


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
                videos = [
                    f for f in video_files_dir.glob("*.*")
                    if f.suffix.lower() in [".mkv", ".mp4", ".ts", ".avi", ".mov", ".webm"]
                ]
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
                "status": status_desc,
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

        rows_data = []
        progress_bars = {}
        for idx, mkv in enumerate(mkv_files):
            parsed = parse_season_episode(mkv.name)
            display_name = f"S{parsed[0]:02d}E{parsed[1]:02d}" if parsed else mkv.stem[:12]
            prog = Progress(
                TextColumn("[yellow]Processing[/yellow]"),
                BarColumn(bar_width=12),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            )
            task_id = prog.add_task("prog", total=100)
            progress_bars[idx] = (prog, task_id)
            rows_data.append({
                "file": display_name,
                "analisis": "[dim]Queued...[/dim]",
                "op_text": "-",
                "ed_text": "-",
                "is_anomaly_op": False,
                "is_anomaly_ed": False,
                "db_status": "[dim]Pending[/dim]",
                "applied": "[dim]Pending[/dim]",
            })

        with Live(
            generate_audit_table(rows_data, title=f"Processing: {target_lib_folder.name}"),
            console=console,
            refresh_per_second=10,
            screen=True,
        ) as live:
            def on_episode_progress(ep_idx: int, _total: int, pct: float) -> None:
                prog, task_id = progress_bars[ep_idx]
                prog.update(task_id, completed=pct)
                rows_data[ep_idx]["analisis"] = prog
                live.update(generate_audit_table(rows_data, title=f"Processing: {target_lib_folder.name}"))

            results = process_series_pipeline(
                series_name=series_name,
                target_lib_folder=target_lib_folder,
                mkv_files=mkv_files,
                wav_templates=wav_templates,
                master_db=master_db,
                plugins_path=plugins_path,
                on_episode_progress=on_episode_progress,
            )

            final_rows = [_build_row_from_result(item) for item in results]
            live.update(generate_audit_table(final_rows, title=f"Processing: {target_lib_folder.name}"))

        console.print(generate_audit_table(final_rows, title=f"Final Audit: {target_lib_folder.name}"))
        console.print()
        console.print(Panel.fit(f"[bold green]✔ Process for {series_name} completed successfully![/bold green]", border_style="green"))
        console.print("\n[cyan]Returning to series selection menu...[/cyan]\n")


if __name__ == "__main__":
    main()