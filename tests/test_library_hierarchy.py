from __future__ import annotations

from backend.infrastructure.config import save_config
from backend.infrastructure.database import init_db
from backend.services.library_service import get_series_detail, scan_library


def test_series_detail_preserves_recursive_user_folder_hierarchy(tmp_path):
    library = tmp_path / "library"
    series = library / "Example Anime"
    files = {
        "S01/Episode 01.mkv": b"episode-1",
        "S01/BD/Extras/Episode 02.mkv": b"episode-2",
        "Final Arc/Part 2/Episode 03.mkv": b"episode-3",
        "Episode 00.mkv": b"special-root",
    }
    for relative_path, content in files.items():
        destination = series / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    save_config({"library_path": str(library)})
    init_db()
    scan_library()

    detail = get_series_detail("Example Anime")
    actual = {episode["file_name"]: episode for episode in detail["episodes"]}

    assert actual["Episode 01.mkv"]["folder_path"] == "S01"
    assert actual["Episode 01.mkv"]["folder_segments"] == ["S01"]
    assert actual["Episode 02.mkv"]["folder_path"] == "S01/BD/Extras"
    assert actual["Episode 02.mkv"]["relative_path"] == "S01/BD/Extras/Episode 02.mkv"
    assert actual["Episode 03.mkv"]["folder_segments"] == ["Final Arc", "Part 2"]
    assert actual["Episode 00.mkv"]["folder_path"] == ""
