from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.api.schemas.models import ArtworkSearchRequest, ArtworkSelection
from backend.services.artwork_service import (
    ArtworkError,
    assign_anilist_poster,
    assign_custom_poster,
    get_poster_file,
    get_series_metadata,
    remove_series_anilist_link,
    remove_series_poster,
    search_anime,
)

router = APIRouter(prefix="/artwork", tags=["Artwork"])


@router.post("/search")
def search(body: ArtworkSearchRequest):
    try:
        return search_anime(body.series_name, body.query)
    except ArtworkError as exc:
        message = str(exc)
        status_code = 429 if "limitado temporalmente" in message.casefold() else 404 if "no existe en la biblioteca" in message.casefold() else 502
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.put("/series/{series_name}/poster")
def select_poster(series_name: str, body: ArtworkSelection):
    try:
        return assign_anilist_poster(series_name, body.anilist_id)
    except ArtworkError as exc:
        message = str(exc)
        status_code = 404 if "no existe en la biblioteca" in message.casefold() or "ya no existe" in message.casefold() else 502
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/series/{series_name}/poster/upload")
def upload_poster(series_name: str, file: UploadFile = File(...)):
    try:
        content = file.file.read(12 * 1024 * 1024 + 1)
        if len(content) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="La imagen supera el límite de 12 MiB.")
        return assign_custom_poster(series_name, content)
    except ArtworkError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        file.file.close()


@router.delete("/series/{series_name}/poster")
def delete_poster(series_name: str):
    remove_series_poster(series_name)
    return {"removed": True, "series_name": series_name}




@router.delete("/series/{series_name}/metadata")
def unlink_series_metadata(series_name: str):
    try:
        remove_series_anilist_link(series_name)
        return {"linked": False, "series_name": series_name}
    except ArtworkError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/series/{series_name}/metadata")
def series_metadata(series_name: str):
    try:
        return get_series_metadata(series_name)
    except ArtworkError as exc:
        message = str(exc)
        status_code = 404 if "no existe en la biblioteca" in message.casefold() else 502
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/series/{series_name}/poster")
def poster(series_name: str):
    result = get_poster_file(series_name)
    if not result:
        raise HTTPException(status_code=404, detail="No hay una carátula local para esta serie.")
    path, media_type = result
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
