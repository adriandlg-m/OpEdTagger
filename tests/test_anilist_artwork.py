from __future__ import annotations

import pytest

from backend.infrastructure.database import db_session, init_db, utc_now
from backend.services import artwork_service


class FakeImageResponse:
    status_code = 200
    ok = True
    headers = {"Content-Type": "image/jpeg", "Content-Length": "9"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size=65536):
        yield bytes.fromhex("ffd8ff") + b"IMAGE!"


@pytest.fixture
def library_series(tmp_path):
    library = tmp_path / "library"
    (library / "Example Anime").mkdir(parents=True)
    from backend.infrastructure.config import save_config
    save_config({"library_path": str(library)})
    init_db()
    with db_session() as conn:
        conn.execute(
            "INSERT INTO series(name,directory,episode_count,last_scanned_at) VALUES(?,?,?,?)",
            ("Example Anime", str(library / "Example Anime"), 0, utc_now()),
        )
    return library


def fake_anilist_media(media_id=123):
    return {
        "id": media_id,
        "title": {"romaji": "Example Anime", "english": "Example Anime", "native": "例示アニメ", "userPreferred": "Example Anime"},
        "format": "TV",
        "seasonYear": 2022,
        "startDate": {"year": 2022},
        "episodes": 12,
        "averageScore": 84,
        "coverImage": {"large": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/example.jpg", "extraLarge": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/example.jpg"},
        "siteUrl": f"https://anilist.co/anime/{media_id}",
    }


def test_search_anime_uses_public_graphql_without_auth(library_series, monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        ok = True
        headers = {}

        def json(self):
            return {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": [fake_anilist_media()]}}}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(artwork_service.requests, "post", fake_post)
    result = artwork_service.search_anime("Example Anime")

    assert result["source"] == "AniList"
    assert result["total"] == 1
    candidate = result["candidates"][0]
    assert candidate["id"] == 123
    assert candidate["title"] == "Example Anime"
    assert candidate["year"] == 2022
    assert candidate["has_poster"] is True
    assert calls[0][0] == "https://graphql.anilist.co"
    assert calls[0][1]["json"]["variables"]["search"] == "Example Anime"
    assert "Authorization" not in calls[0][1]["headers"]




@pytest.mark.parametrize(
    ("raw_title", "expected"),
    [
        ("Dan Dan Dan (2019)", "Dan Dan Dan"),
        ("  Dan   Dan Dan (2019)  ", "Dan Dan Dan"),
        ("Dan Dan Dan [2019]", "Dan Dan Dan"),
        ("Dan Dan Dan {2019}", "Dan Dan Dan"),
        ("86 (2021)", "86"),
        ("Dan Dan Dan", "Dan Dan Dan"),
        ("2001: A Space Odyssey", "2001: A Space Odyssey"),
        ("Anime (Season 2)", "Anime (Season 2)"),
    ],
)
def test_normalize_anilist_search_title_removes_only_trailing_release_year(raw_title, expected):
    assert artwork_service.normalize_anilist_search_title(raw_title) == expected


def test_search_anime_omits_trailing_folder_year_without_changing_original_query(library_series, monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        ok = True
        headers = {}

        def json(self):
            return {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": [fake_anilist_media()]}}}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(artwork_service.requests, "post", fake_post)
    result = artwork_service.search_anime("Example Anime", "Dan Dan Dan (2019)")

    assert calls[0][1]["json"]["variables"]["search"] == "Dan Dan Dan"
    assert result["query"] == "Dan Dan Dan"
    assert result["original_query"] == "Dan Dan Dan (2019)"
    assert result["year_suffix_removed"] is True


def test_search_anime_uses_folder_name_without_trailing_year_when_query_is_omitted(library_series, monkeypatch):
    folder_name = "Dan Dan Dan (2019)"
    (library_series / folder_name).mkdir(parents=True)
    with db_session() as conn:
        conn.execute(
            "INSERT INTO series(name,directory,episode_count,last_scanned_at) VALUES(?,?,?,?)",
            (folder_name, str(library_series / folder_name), 0, utc_now()),
        )

    calls = []

    class FakeResponse:
        status_code = 200
        ok = True
        headers = {}

        def json(self):
            return {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": [fake_anilist_media()]}}}

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return FakeResponse()

    monkeypatch.setattr(artwork_service.requests, "post", fake_post)
    result = artwork_service.search_anime(folder_name)

    assert calls[0]["json"]["variables"]["search"] == "Dan Dan Dan"
    assert result["original_query"] == folder_name
    assert result["query"] == "Dan Dan Dan"
    assert result["year_suffix_removed"] is True


def test_search_anime_without_year_preserves_query(library_series, monkeypatch):
    class FakeResponse:
        status_code = 200
        ok = True
        headers = {}

        def json(self):
            return {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": []}}}

    monkeypatch.setattr(artwork_service.requests, "post", lambda *args, **kwargs: FakeResponse())
    result = artwork_service.search_anime("Example Anime", "Dan Dan Dan")
    assert result["query"] == "Dan Dan Dan"
    assert result["original_query"] == "Dan Dan Dan"
    assert result["year_suffix_removed"] is False


def test_search_anime_rejects_untrusted_preview_urls(library_series, monkeypatch):
    class FakeResponse:
        status_code = 200
        ok = True
        headers = {}

        def json(self):
            media = fake_anilist_media()
            media["coverImage"] = {"large": "https://evil.example/file/anilistcdn/x.jpg"}
            return {"data": {"Page": {"pageInfo": {}, "media": [media]}}}

    monkeypatch.setattr(artwork_service.requests, "post", lambda *args, **kwargs: FakeResponse())
    result = artwork_service.search_anime("Example Anime")
    assert result["candidates"][0]["has_poster"] is False
    assert result["candidates"][0]["poster_preview_url"] is None


def test_assign_anilist_poster_downloads_and_links_selected_artwork(library_series, monkeypatch):
    class FakeGraphqlResponse:
        status_code = 200
        ok = True
        headers = {}

        def json(self):
            return {"data": {"Media": fake_anilist_media(55)}}

    monkeypatch.setattr(artwork_service.requests, "post", lambda *args, **kwargs: FakeGraphqlResponse())
    monkeypatch.setattr(artwork_service.requests, "get", lambda *args, **kwargs: FakeImageResponse())

    result = artwork_service.assign_anilist_poster("Example Anime", 55)
    assert result["provider"] == "anilist"
    assert result["external_id"] == "55"
    assert result["selected_title"] == "Example Anime"
    assert result["poster_url"].startswith("/api/artwork/series/Example%20Anime/poster")

    poster = artwork_service.get_poster_file("Example Anime")
    assert poster is not None
    path, media_type = poster
    assert path.is_file()
    assert media_type == "image/jpeg"
    assert path.read_bytes().startswith(bytes.fromhex("ffd8ff"))

    with db_session() as conn:
        row = conn.execute(
            "SELECT provider,external_id,poster_file FROM series_artwork WHERE series_name=?",
            ("Example Anime",),
        ).fetchone()
    assert row["provider"] == "anilist"
    assert row["external_id"] == "55"
    assert row["poster_file"].startswith("posters/anilist/")

    from backend.services.library_service import get_series_detail, list_series
    dashboard = list_series(scan=False)
    assert dashboard["series"][0]["poster_url"].startswith("/api/artwork/series/Example%20Anime/poster")
    detail = get_series_detail("Example Anime")
    assert detail["poster_url"].startswith("/api/artwork/series/Example%20Anime/poster")


def test_local_custom_poster_is_validated_and_stored(library_series):
    result = artwork_service.assign_custom_poster("Example Anime", bytes.fromhex("ffd8ff") + b"CUSTOM")
    assert result["provider"] == "local"
    assert result["external_id"] is None
    poster = artwork_service.get_poster_file("Example Anime")
    assert poster is not None
    assert poster[1] == "image/jpeg"

    with pytest.raises(artwork_service.ArtworkError, match="JPEG, PNG o WebP"):
        artwork_service.assign_custom_poster("Example Anime", b"<svg></svg>")


def test_remove_artwork_removes_metadata_and_managed_cache(library_series, monkeypatch):
    class FakeGraphqlResponse:
        status_code = 200
        ok = True
        headers = {}

        def json(self):
            return {"data": {"Media": fake_anilist_media(77)}}

    monkeypatch.setattr(artwork_service.requests, "post", lambda *args, **kwargs: FakeGraphqlResponse())
    monkeypatch.setattr(artwork_service.requests, "get", lambda *args, **kwargs: FakeImageResponse())
    artwork_service.assign_anilist_poster("Example Anime", 77)
    path, _ = artwork_service.get_poster_file("Example Anime")

    artwork_service.remove_series_poster("Example Anime")

    assert artwork_service.get_poster_file("Example Anime") is None
    assert not path.exists()


def test_legacy_tmdb_poster_is_migrated_without_secret(library_series, tmp_path):
    from backend.infrastructure.config import app_data_dir
    poster_dir = app_data_dir() / "posters" / "tmdb"
    poster_dir.mkdir(parents=True, exist_ok=True)
    poster_path = poster_dir / "legacy.jpg"
    poster_path.write_bytes(bytes.fromhex("ffd8ff") + b"OLD")
    relative = "posters/tmdb/legacy.jpg"
    with db_session() as conn:
        # Simulate an installation from before the provider-neutral artwork migration.
        conn.execute("DELETE FROM schema_migrations WHERE version=2")
        conn.execute(
            "INSERT INTO series_metadata(series_name,tmdb_id,tmdb_name,tmdb_original_name,poster_file,poster_path,updated_at) VALUES(?,?,?,?,?,?,?)",
            ("Example Anime", 99, "Legacy TMDb title", "Legacy original", relative, "/legacy.jpg", utc_now()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO integration_secrets(provider,encrypted_value,updated_at) VALUES('tmdb','legacy-secret',?)",
            (utc_now(),),
        )

    init_db()

    with db_session() as conn:
        row = conn.execute("SELECT provider,external_id,poster_file FROM series_artwork WHERE series_name='Example Anime'").fetchone()
        secret = conn.execute("SELECT 1 FROM integration_secrets WHERE provider='tmdb'").fetchone()
    assert row["provider"] == "tmdb_legacy"
    assert row["external_id"] == "99"
    assert row["poster_file"] == relative
    assert secret is None
    assert artwork_service.get_poster_file("Example Anime")[0] == poster_path


def test_artwork_api_search_route_has_no_token_requirement(library_series, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.api.routers import artwork as artwork_router
    from backend.main import app

    expected = {"query": "Example Anime", "total": 1, "has_next_page": False, "candidates": [{"id": 7, "title": "Example Anime"}], "source": "AniList"}
    monkeypatch.setattr(artwork_router, "search_anime", lambda *args, **kwargs: expected)

    with TestClient(app) as client:
        response = client.post("/api/artwork/search", json={"series_name": "Example Anime"})
    assert response.status_code == 200
    assert response.json() == expected


def test_legacy_artwork_migration_is_not_replayed_after_user_removal(library_series, tmp_path):
    from backend.infrastructure.config import app_data_dir
    poster_dir = app_data_dir() / "posters" / "tmdb"
    poster_dir.mkdir(parents=True, exist_ok=True)
    poster_path = poster_dir / "legacy-once.jpg"
    poster_path.write_bytes(bytes.fromhex("ffd8ff") + b"OLD")
    relative = "posters/tmdb/legacy-once.jpg"
    with db_session() as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version=2")
        conn.execute(
            "INSERT INTO series_metadata(series_name,tmdb_id,tmdb_name,tmdb_original_name,poster_file,poster_path,updated_at) VALUES(?,?,?,?,?,?,?)",
            ("Example Anime", 101, "Legacy Once", "Legacy Original", relative, "/legacy-once.jpg", utc_now()),
        )
    init_db()
    assert artwork_service.get_poster_file("Example Anime") is not None

    artwork_service.remove_series_poster("Example Anime")
    init_db()

    assert artwork_service.get_poster_file("Example Anime") is None
    assert not poster_path.exists()


def test_image_signatures_are_detected_without_escape_literal_mismatch():
    assert artwork_service._detect_image_type(bytes.fromhex("ffd8ff") + b"jpeg") == "image/jpeg"
    assert artwork_service._detect_image_type(bytes.fromhex("89504e470d0a1a0a") + b"png") == "image/png"
    assert artwork_service._detect_image_type(b"RIFF" + b"1234" + b"WEBP" + b"data") == "image/webp"
    with pytest.raises(artwork_service.ArtworkError):
        artwork_service._detect_image_type(b"not an image")


def test_anilist_image_url_rejects_credentials_and_nonstandard_ports():
    assert artwork_service._valid_anilist_image_url(
        "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/a.jpg"
    )
    assert artwork_service._valid_anilist_image_url(
        "https://user@s4.anilist.co/file/anilistcdn/media/anime/cover/large/a.jpg"
    ) is None
    assert artwork_service._valid_anilist_image_url(
        "https://s4.anilist.co:8443/file/anilistcdn/media/anime/cover/large/a.jpg"
    ) is None


def test_replacing_poster_generates_a_new_cache_buster(library_series, monkeypatch):
    class FakeGraphqlResponse:
        status_code = 200
        ok = True
        headers = {}

        def json(self):
            return {"data": {"Media": fake_anilist_media(88)}}

    monkeypatch.setattr(artwork_service.requests, "post", lambda *args, **kwargs: FakeGraphqlResponse())
    monkeypatch.setattr(artwork_service.requests, "get", lambda *args, **kwargs: FakeImageResponse())
    first = artwork_service.assign_anilist_poster("Example Anime", 88)
    second = artwork_service.assign_anilist_poster("Example Anime", 88)
    assert first["poster_url"] != second["poster_url"]


def test_anilist_assignment_creates_dedicated_metadata_link(library_series, monkeypatch):
    class FakeGraphqlResponse:
        status_code = 200
        ok = True
        headers = {}

        def json(self):
            return {"data": {"Media": fake_anilist_media(2024)}}

    monkeypatch.setattr(artwork_service.requests, "post", lambda *args, **kwargs: FakeGraphqlResponse())
    monkeypatch.setattr(artwork_service.requests, "get", lambda *args, **kwargs: FakeImageResponse())
    artwork_service.assign_anilist_poster("Example Anime", 2024)

    with db_session() as conn:
        row = conn.execute(
            "SELECT anilist_id FROM series_anilist_links WHERE series_name=?",
            ("Example Anime",),
        ).fetchone()
    assert row["anilist_id"] == 2024


def test_custom_poster_keeps_existing_anilist_metadata_link(library_series, monkeypatch):
    class FakeGraphqlResponse:
        status_code = 200
        ok = True
        headers = {}

        def json(self):
            return {"data": {"Media": fake_anilist_media(2025)}}

    monkeypatch.setattr(artwork_service.requests, "post", lambda *args, **kwargs: FakeGraphqlResponse())
    monkeypatch.setattr(artwork_service.requests, "get", lambda *args, **kwargs: FakeImageResponse())
    artwork_service.assign_anilist_poster("Example Anime", 2025)
    artwork_service.assign_custom_poster("Example Anime", bytes.fromhex("ffd8ff") + b"LOCAL")

    with db_session() as conn:
        link = conn.execute(
            "SELECT anilist_id FROM series_anilist_links WHERE series_name=?",
            ("Example Anime",),
        ).fetchone()
        artwork = conn.execute(
            "SELECT provider FROM series_artwork WHERE series_name=?",
            ("Example Anime",),
        ).fetchone()
    assert link["anilist_id"] == 2025
    assert artwork["provider"] == "local"


def test_get_series_metadata_fetches_live_details_and_sanitizes_description(library_series, monkeypatch):
    anilist_id = 45678
    with db_session() as conn:
        conn.execute(
            "INSERT INTO series_anilist_links(series_name,anilist_id,linked_at) VALUES(?,?,?)",
            ("Example Anime", anilist_id, utc_now()),
        )

    monkeypatch.setattr(artwork_service, "_METADATA_CACHE", {})
    calls = []

    def fake_get_details(media_id):
        calls.append(media_id)
        return {
            "id": media_id,
            "title": {"userPreferred": "Example Anime", "english": "Example Anime", "native": "例示", "romaji": "Example Anime"},
            "description": "<p>A <strong>short</strong> synopsis.</p><script>do_not_render()</script><p>Second paragraph.</p>",
            "format": "TV",
            "season": "FALL",
            "seasonYear": 2022,
            "startDate": {"year": 2022, "month": 10, "day": 1},
            "episodes": 12,
            "duration": 24,
            "countryOfOrigin": "JP",
            "genres": ["Action", "Comedy"],
            "bannerImage": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/45678-example.jpg",
            "coverImage": {"extraLarge": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/45678-example.jpg"},
        }

    monkeypatch.setattr(artwork_service, "_get_anime_details", fake_get_details)
    result = artwork_service.get_series_metadata("Example Anime")

    assert result["linked"] is True
    metadata = result["metadata"]
    assert metadata["anilist_id"] == anilist_id
    assert metadata["title"] == "Example Anime"
    assert metadata["year"] == 2022
    assert metadata["format"] == "TV"
    assert metadata["episodes"] == 12
    assert metadata["genres"] == ["Action", "Comedy"]
    assert metadata["banner_url"].startswith("https://s4.anilist.co/file/anilistcdn/")
    assert "short synopsis" in metadata["description"]
    assert "Second paragraph" in metadata["description"]
    assert "do_not_render" not in metadata["description"]
    assert "<strong>" not in metadata["description"]
    assert calls == [anilist_id]

    # A second visit within the short in-memory TTL must not call AniList again.
    again = artwork_service.get_series_metadata("Example Anime")
    assert again["metadata"]["anilist_id"] == anilist_id
    assert calls == [anilist_id]


def test_get_series_metadata_without_link_does_not_call_anilist(library_series, monkeypatch):
    monkeypatch.setattr(
        artwork_service,
        "_get_anime_details",
        lambda _: (_ for _ in ()).throw(AssertionError("AniList should not be called")),
    )
    result = artwork_service.get_series_metadata("Example Anime")
    assert result == {"linked": False, "metadata": None, "source": None}


def test_schema_v3_migrates_existing_anilist_artwork_association(library_series):
    with db_session() as conn:
        conn.execute(
            "INSERT INTO series_artwork(series_name,provider,external_id,selected_title,original_title,poster_file,source_image_url,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("Example Anime", "anilist", "9988", "Example Anime", "Original", "posters/anilist/example.jpg", None, utc_now()),
        )
        conn.execute("DELETE FROM schema_migrations WHERE version=3")

    from backend.infrastructure.database import init_db
    init_db()

    with db_session() as conn:
        link = conn.execute(
            "SELECT anilist_id FROM series_anilist_links WHERE series_name=?",
            ("Example Anime",),
        ).fetchone()
        migration = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=3"
        ).fetchone()
    assert link["anilist_id"] == 9988
    assert migration is not None


def test_unlinking_anilist_metadata_keeps_selected_local_poster(library_series):
    artwork_service.assign_custom_poster(
        "Example Anime", bytes.fromhex("ffd8ff") + b"KEEP-LOCAL-POSTER"
    )
    with db_session() as conn:
        conn.execute(
            "INSERT INTO series_anilist_links(series_name,anilist_id,linked_at) VALUES(?,?,?)",
            ("Example Anime", 321, utc_now()),
        )

    artwork_service.remove_series_anilist_link("Example Anime")

    assert artwork_service.get_series_metadata("Example Anime")["linked"] is False
    poster = artwork_service.get_poster_file("Example Anime")
    assert poster is not None
    assert poster[0].read_bytes().startswith(bytes.fromhex("ffd8ff"))


def test_assigning_anilist_poster_seeds_temporary_metadata_cache(library_series, monkeypatch):
    calls = []
    media = {
        "id": 65432,
        "title": {"userPreferred": "Cached Anime", "english": "Cached Anime", "native": "キャッシュ"},
        "description": "A cached synopsis.",
        "format": "TV",
        "seasonYear": 2024,
        "episodes": 12,
        "duration": 24,
        "countryOfOrigin": "JP",
        "genres": ["Drama"],
        "coverImage": {"extraLarge": "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/cache.jpg"},
        "bannerImage": "https://s4.anilist.co/file/anilistcdn/media/anime/banner/cache.jpg",
    }

    def fake_details(anilist_id):
        calls.append(anilist_id)
        return media

    monkeypatch.setattr(artwork_service, "_METADATA_CACHE", {})
    monkeypatch.setattr(artwork_service, "_get_anime_details", fake_details)
    monkeypatch.setattr(artwork_service, "_download_anilist_image", lambda _: (bytes.fromhex("ffd8ff") + b"CACHE", "image/jpeg"))

    artwork_service.assign_anilist_poster("Example Anime", 65432)
    result = artwork_service.get_series_metadata("Example Anime")

    assert result["metadata"]["description"] == "A cached synopsis."
    assert result["metadata"]["banner_url"].endswith("cache.jpg")
    assert calls == [65432]
