from __future__ import annotations

from pydantic import BaseModel, Field


class SeriesJobRequest(BaseModel):
    skip_indices: list[int] = Field(default_factory=list)
    selected_anime_slug: str | None = None


class TemplateSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=180)


class TemplateDownloadRequest(BaseModel):
    series_name: str = Field(min_length=1, max_length=240)
    anime_slug: str = Field(min_length=1, max_length=240)


class SettingsUpdate(BaseModel):
    library_path: str | None = None
    template_path: str | None = None
    plugins_path: str | None = None
    backup_path: str | None = None
    auto_download_templates: bool | None = None


class ReviewUpdate(BaseModel):
    op: dict | None = None
    ed: dict | None = None
    approved: bool = False


class InjectionRequest(BaseModel):
    confirm: bool = False
    replace_existing_chapters: bool = False


class ArtworkSearchRequest(BaseModel):
    series_name: str = Field(min_length=1, max_length=240)
    query: str | None = Field(default=None, max_length=180)


class ArtworkSelection(BaseModel):
    anilist_id: int = Field(gt=0)


class BatchInjectionRequest(BaseModel):
    episode_ids: list[int] = Field(min_length=1, max_length=500)
    replace_existing_chapters: bool = False
