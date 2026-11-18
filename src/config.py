# src/config.py
import json
import sys
from pathlib import Path
from rich.console import Console

console = Console()
CONFIG_FILE = Path(__file__).parent.parent / "config.json"

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        console.print(f"[bold red]✘ Configuration file not found at: {CONFIG_FILE}[/bold red]")
        sys.exit(1)
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)