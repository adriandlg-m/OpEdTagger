# src/processor.py
import statistics
from rich.console import Console
from rich.live import Live
from rich.progress import Progress, BarColumn, TextColumn

from src.database import save_master_db
from src.analyzer import evaluate_episode_templates
from src.mkv_tools import apply_mkv_chapters_from_db
from src.ui import generate_audit_table, format_compact_timestamp, extract_short_tag
from src.utils import parse_season_episode, write_audit_log

console = Console()

def process_series_pipeline(series_name: str, target_lib_folder, mkv_files, wav_templates, master_db, plugins_path):
    if series_name not in master_db:
        master_db[series_name] = {}

    rows_data = []
    for mkv in mkv_files:
        parsed = parse_season_episode(mkv.name)
        display_name = f"S{parsed[0]:02d}E{parsed[1]:02d}" if parsed else mkv.stem[:12]
        rows_data.append({
            "file": display_name,
            "analisis": "[dim]Queued...[/dim]",
            "op_text": "-",
            "ed_text": "-",
            "raw_op_start": None,
            "raw_ed_start": None,
            "is_anomaly_op": False,
            "is_anomaly_ed": False,
            "db_status": "[dim]Pending[/dim]",
            "applied": "[dim]Pending[/dim]"
        })

    with Live(generate_audit_table(rows_data, title=f"Processing: {target_lib_folder.name}"), console=console, refresh_per_second=10, screen=True) as live:
        for idx, mkv in enumerate(mkv_files):
            parsed = parse_season_episode(mkv.name)
            display_name = f"S{parsed[0]:02d}E{parsed[1]:02d}" if parsed else mkv.stem[:12]

            cached_episode = master_db.get(series_name, {}).get(mkv.name)

            if cached_episode:
                op_data = cached_episode.get("op")
                ed_data = cached_episode.get("ed")
                
                op_tag = extract_short_tag(op_data["name"]) if op_data else None
                ed_tag = extract_short_tag(ed_data["name"]) if ed_data else None

                if op_tag and ed_tag:
                    analysis_status = f"[cyan]⚡ Cache: {op_tag} + {ed_tag}[/cyan]"
                elif op_tag:
                    analysis_status = f"[cyan]⚡ Cache: Only {op_tag}[/cyan]"
                elif ed_tag:
                    analysis_status = f"[cyan]⚡ Cache: Only {ed_tag}[/cyan]"
                else:
                    analysis_status = "[cyan]⚡ Cache: No match[/cyan]"

                op_text = f"{format_compact_timestamp(op_data['start'])} - {format_compact_timestamp(op_data['end'])} [{op_data['score']:.2f}]" if op_data else None
                ed_text = f"{format_compact_timestamp(ed_data['start'])} - {format_compact_timestamp(ed_data['end'])} [{ed_data['score']:.2f}]" if ed_data else None
                raw_op_start = op_data['start'] if op_data else None
                raw_ed_start = ed_data['start'] if ed_data else None
                db_status_text = "[blue]✔ Cache[/blue]"

                success, error_msg = apply_mkv_chapters_from_db(mkv, cached_episode, plugins_path)
                applied_status = "[green]✔ Applied[/green]" if success else "[red]✘ Error[/red]"
                if not success:
                    write_audit_log(series_name, mkv.name, f"Error applying chapters from cache: {error_msg}")

            else:
                prog = Progress(
                    TextColumn("[yellow]Processing[/yellow]"),
                    BarColumn(bar_width=12),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                )
                task_id = prog.add_task("prog", total=100)

                rows_data[idx]["analisis"] = prog
                live.update(generate_audit_table(rows_data, title=f"Processing: {target_lib_folder.name}"))

                def update_prog(pct):
                    prog.update(task_id, completed=pct)
                    live.update(generate_audit_table(rows_data, title=f"Processing: {target_lib_folder.name}"))

                op_candidates, ed_candidates = evaluate_episode_templates(mkv, wav_templates, plugins_path, update_prog)

                best_op = max(op_candidates, key=lambda x: x["score"]) if op_candidates else None
                best_ed = max(ed_candidates, key=lambda x: x["score"]) if ed_candidates else None

                op_text, ed_text = None, None
                raw_op_start, raw_ed_start = None, None
                op_tag, ed_tag = None, None

                if best_op:
                    op_tag = extract_short_tag(best_op["name"])
                    op_text = f"{format_compact_timestamp(best_op['start'])} - {format_compact_timestamp(best_op['end'])} [{best_op['score']:.2f}]"
                    raw_op_start = best_op["start"]

                if best_ed:
                    ed_tag = extract_short_tag(best_ed["name"])
                    ed_text = f"{format_compact_timestamp(best_ed['start'])} - {format_compact_timestamp(best_ed['end'])} [{best_ed['score']:.2f}]"
                    raw_ed_start = best_ed["start"]

                if op_tag and ed_tag:
                    analysis_status = f"[green]✔ {op_tag} + {ed_tag}[/green]"
                elif op_tag:
                    analysis_status = f"[yellow]⚠ Only {op_tag}[/yellow]"
                    write_audit_log(series_name, mkv.name, f"Alert: Only {op_tag} detected, ED missing.")
                elif ed_tag:
                    analysis_status = f"[yellow]⚠ Only {ed_tag}[/yellow]"
                    write_audit_log(series_name, mkv.name, f"Alert: Only {ed_tag} detected, OP missing.")
                else:
                    analysis_status = "[red]✘ No match[/red]"
                    write_audit_log(series_name, mkv.name, "Critical Alert: No template exceeded the threshold.")

                master_db[series_name][mkv.name] = {
                    "op": {
                        "name": best_op["name"], "start": best_op["start"], "end": best_op["end"], "score": best_op["score"]
                    } if best_op else None,
                    "ed": {
                        "name": best_ed["name"], "start": best_ed["start"], "end": best_ed["end"], "score": best_ed["score"]
                    } if best_ed else None
                }
                save_master_db(master_db)
                db_status_text = "[green]✔ Saved[/green]"

                applied_status = "[red]✘ Error[/red]"
                if best_op or best_ed:
                    episode_record_from_db = master_db[series_name][mkv.name]
                    success, error_msg = apply_mkv_chapters_from_db(mkv, episode_record_from_db, plugins_path)
                    if success:
                        applied_status = "[green]✔ Applied[/green]"
                    else:
                        applied_status = "[red]✘ MKV Error[/red]"
                        write_audit_log(series_name, mkv.name, f"Error injecting chapters into MKV: {error_msg}")
                else:
                    applied_status = "[dim]N/A[/dim]"

            rows_data[idx] = {
                "file": display_name,
                "analisis": analysis_status,
                "op_text": op_text,
                "ed_text": ed_text,
                "raw_op_start": raw_op_start,
                "raw_ed_start": raw_ed_start,
                "is_anomaly_op": False,
                "is_anomaly_ed": False,
                "db_status": db_status_text,
                "applied": applied_status
            }
            live.update(generate_audit_table(rows_data, title=f"Processing: {target_lib_folder.name}"))

    # --- POST-PROCESSING: ANOMALY DETECTION (Outliers) ---
    op_starts = [r["raw_op_start"] for r in rows_data if r["raw_op_start"] is not None]
    ed_starts = [r["raw_ed_start"] for r in rows_data if r["raw_ed_start"] is not None]

    if op_starts:
        median_op = statistics.median(op_starts)
        for row in rows_data:
            if row["raw_op_start"] is not None and abs(row["raw_op_start"] - median_op) > 180: # Deviation > 3 minutes
                row["is_anomaly_op"] = True

    if ed_starts:
        median_ed = statistics.median(ed_starts)
        for row in rows_data:
            if row["raw_ed_start"] is not None and abs(row["raw_ed_start"] - median_ed) > 180: # Deviation > 3 minutes
                row["is_anomaly_ed"] = True

    # --- STATIC FINAL SNAPSHOT ---
    console.print(generate_audit_table(rows_data, title=f"Final Audit: {target_lib_folder.name}"))