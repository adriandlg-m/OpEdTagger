# src/mkv_tools.py
import os
import random
import subprocess
import tempfile
from pathlib import Path

def seconds_to_mkvtoolnix_time(seconds: float) -> str:
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:012.9f}"

def find_mkvpropedit_executable(plugins_path: Path) -> Path | None:
    matches = list(plugins_path.rglob("mkvpropedit.exe"))
    if matches:
        return matches[0]
    return None

def get_mkv_duration(mkv_path: Path, plugins_path: Path) -> float:
    ffprobe_exe = plugins_path / "FFMPEG" / "bin" / "ffprobe.exe"
    if ffprobe_exe.exists():
        try:
            cmd = [
                str(ffprobe_exe), "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(mkv_path)
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode == 0 and res.stdout.strip():
                return float(res.stdout.strip())
        except Exception:
            pass
    return 1440.0

def apply_mkv_chapters_from_db(mkv_path: Path, episode_db_record: dict, plugins_path: Path) -> tuple[bool, str]:
    mkvpropedit_exe = find_mkvpropedit_executable(plugins_path)
    if not mkvpropedit_exe or not mkvpropedit_exe.exists():
        return False, "mkvpropedit not found in plugins"

    op_data = episode_db_record.get("op")
    ed_data = episode_db_record.get("ed")

    if not op_data and not ed_data:
        return False, "No data in DB"

    total_duration = get_mkv_duration(mkv_path, plugins_path)
    raw_chapters = []
    current_time = 0.0

    op_start = op_data["start"] if op_data else None
    ed_start = ed_data["start"] if ed_data else None

    if op_start is not None and op_start > 1.0:
        raw_chapters.append(("Prologue", 0.0, op_start))
        current_time = op_start
    elif op_start is not None and op_start <= 1.0:
        current_time = 0.0

    if op_data:
        start_t = current_time
        if not raw_chapters and start_t > 0.0:
            raw_chapters.append(("Prologue", 0.0, op_start))
            start_t = op_start
        raw_chapters.append((op_data["name"], start_t, op_data["end"]))
        current_time = op_data["end"]

    if ed_start is not None:
        if current_time < ed_start:
            raw_chapters.append(("Episode", current_time, ed_start))
            current_time = ed_start
    else:
        if current_time < total_duration:
            raw_chapters.append(("Episode", current_time, total_duration))
            current_time = total_duration

    if ed_data:
        start_t = ed_data["start"]
        if start_t > current_time:
            raw_chapters.append(("Episode", current_time, start_t))
        raw_chapters.append((ed_data["name"], start_t, ed_data["end"]))
        current_time = ed_data["end"]

    if current_time < total_duration - 1.0:
        raw_chapters.append(("Ending", current_time, total_duration))

    if not raw_chapters:
        return False, "No valid chapters"

    edition_uid = random.randint(1000000000000000000, 9999999999999999999)
    xml_content = '<?xml version="1.0"?>\n'
    xml_content += '<Chapters>\n'
    xml_content += '  <EditionEntry>\n'
    xml_content += '    <EditionFlagHidden>0</EditionFlagHidden>\n'
    xml_content += '    <EditionFlagDefault>1</EditionFlagDefault>\n'
    xml_content += f'    <EditionUID>{edition_uid}</EditionUID>\n'

    for name, start_sec, end_sec in raw_chapters:
        start_str = seconds_to_mkvtoolnix_time(start_sec)
        end_str = seconds_to_mkvtoolnix_time(end_sec)
        chapter_uid = random.randint(1000000000000000000, 9999999999999999999)
        
        xml_content += '    <ChapterAtom>\n'
        xml_content += f'      <ChapterTimeStart>{start_str}</ChapterTimeStart>\n'
        xml_content += f'      <ChapterTimeEnd>{end_str}</ChapterTimeEnd>\n'
        xml_content += f'      <ChapterUID>{chapter_uid}</ChapterUID>\n'
        xml_content += f'      <ChapterDisplay>\n'
        xml_content += f'        <ChapterString>{name}</ChapterString>\n'
        xml_content += f'        <ChapterLanguage>spa</ChapterLanguage>\n'
        xml_content += f'        <ChapLanguageIETF>es</ChapLanguageIETF>\n'
        xml_content += f'      </ChapterDisplay>\n'
        xml_content += f'    </ChapterAtom>\n'