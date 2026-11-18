# src/ui.py
import re
from rich.table import Table

def format_compact_timestamp(seconds: float) -> str:
    """Formato compacto sin milisegundos para evitar saturación visual (ej. 00:00 - 01:29)."""
    if seconds is None:
        return "N/A"
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"

def extract_short_tag(full_name: str) -> str:
    """Extrae solo la etiqueta corta (ej. 'OP1', 'ED2') de nombres largos."""
    if not full_name:
        return ""
    match = re.search(r'(OP\d+|ED\d+|SP\d+)', full_name, re.IGNORECASE)
    return match.group(1).upper() if match else full_name[:8]

def generate_audit_table(rows_data, title="Auditoría, DB e Inyección MKV"):
    table = Table(title=title, border_style="blue", expand=True)
    table.add_column("Video File", style="cyan", no_wrap=True)
    table.add_column("Analisis", style="white", no_wrap=True)
    table.add_column("OP Data", style="green", no_wrap=True)
    table.add_column("ED Data", style="magenta", no_wrap=True)
    table.add_column("DB Status", style="blue", no_wrap=True)
    table.add_column("Timestamp Aplicado", style="yellow", no_wrap=True)

    for row in rows_data:
        # Si la fila está marcada como anomalía, resaltamos los datos de audio en amarillo
        op_style = "yellow" if row.get("is_anomaly_op") else "green"
        ed_style = "yellow" if row.get("is_anomaly_ed") else "magenta"
        
        op_text = f"[{op_style}]{row['op_text']}[/{op_style}]" if row.get("op_text") else "[dim]N/A[/dim]"
        ed_text = f"[{ed_style}]{row['ed_text']}[/{ed_style}]" if row.get("ed_text") else "[dim]N/A[/dim]"

        table.add_row(
            str(row["file"]), 
            row["analisis"], 
            op_text, 
            ed_text, 
            str(row["db_status"]), 
            str(row["applied"])
        )
    return table