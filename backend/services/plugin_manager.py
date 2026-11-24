from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import requests

from backend.infrastructure.config import load_config

logger = logging.getLogger(__name__)
_INSTALL_LOCK = threading.RLock()
_STATE_LOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "status": "not_checked",
    "last_error": None,
    "last_check": None,
    "installed": {},
}

# The FFmpeg archive is resolved from the official FFmpeg-Builds GitHub
# release metadata and its published SHA-256 digest. The exact asset digest
# is read from the same release response before the archive is downloaded.
FFMPEG_RELEASE_API = "https://api.github.com/repos/FFmpeg-Builds/FFmpeg-Builds/releases/latest"
FFMPEG_ASSET_NAME = "ffmpeg-master-latest-win64-gpl.zip"

# MKVToolNix 104.0 is the current stable Windows release at the time this
# installer was implemented. Its checksum is published by the official
# MKVToolNix download site.
MKVTOOLNIX_VERSION = "104.0"
MKVTOOLNIX_ARCHIVE = f"mkvtoolnix-64-bit-{MKVTOOLNIX_VERSION}.7z"
MKVTOOLNIX_URL = (
    f"https://mkvtoolnix.download/windows/releases/{MKVTOOLNIX_VERSION}/{MKVTOOLNIX_ARCHIVE}"
)
MKVTOOLNIX_SHA256 = "4d14b615819d2e106893d68114e45f7bbe8092dcb4c7c3599a0c0a52313321a9"


def _tool_candidates(root: Path, name: str) -> list[Path]:
    filenames = (f"{name}.exe", name) if os.name == "nt" else (name, f"{name}.exe")
    found: list[Path] = []
    if root.exists():
        for filename in filenames:
            found.extend(root.rglob(filename))
    return sorted((item for item in found if item.is_file()), key=lambda item: len(item.parts))


def find_local_tool(root: Path, name: str) -> Path | None:
    candidates = _tool_candidates(root, name)
    return candidates[0] if candidates else None


def _version_ok(path: Path, args: tuple[str, ...] = ("-version",)) -> tuple[bool, str | None]:
    try:
        result = subprocess.run(
            [str(path), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            output = (result.stdout or result.stderr or "").strip().splitlines()
            return True, output[0][:240] if output else "available"
        return False, (result.stderr or result.stdout or "non-zero exit code")[-500:]
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path, *, timeout: tuple[int, int] = (20, 180), attempts: int = 3) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            existing = partial.stat().st_size if partial.exists() else 0
            headers = {"Range": f"bytes={existing}-"} if existing else {}
            with requests.get(url, headers=headers, stream=True, timeout=timeout) as response:
                if response.status_code == 206 and existing:
                    content_range = response.headers.get("Content-Range", "")
                    if content_range and not content_range.startswith(f"bytes {existing}-"):
                        partial.unlink(missing_ok=True)
                        raise RuntimeError("The server returned an inconsistent byte range.")
                    mode = "ab"
                elif response.status_code == 200:
                    mode = "wb"
                elif response.status_code == 416:
                    partial.unlink(missing_ok=True)
                    raise RuntimeError("The server rejected the resume offset; restarting the download.")
                else:
                    response.raise_for_status()
                    mode = "wb"

                with partial.open(mode) as stream:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            stream.write(chunk)

            if not partial.exists() or partial.stat().st_size == 0:
                raise RuntimeError("The downloaded archive is empty.")
            partial.replace(destination)
            return
        except Exception as exc:
            last_error = exc
            logger.warning("Download attempt %s/%s failed for %s: %s", attempt, attempts, url, exc)
            if attempt < attempts:
                # Exponential backoff without blocking on a long fixed delay.
                import time
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"Could not download '{url}' after {attempts} attempts: {last_error}") from last_error


def _safe_member_path(root: Path, raw_name: str) -> Path:
    normalized = raw_name.replace("\\", "/")
    member = PurePosixPath(normalized)
    if member.is_absolute() or not member.parts or any(part in {"", ".", ".."} for part in member.parts):
        raise ValueError(f"Unsafe archive member path: {raw_name!r}")
    if ":" in member.parts[0]:
        raise ValueError(f"Drive-qualified archive member path is not allowed: {raw_name!r}")
    target = (root / Path(*member.parts)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Archive member escapes the installation directory: {raw_name!r}") from exc
    return target


def _extract_zip_safely(archive_path: Path, target_dir: Path) -> None:
    import zipfile

    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            _safe_member_path(target_dir, info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"Symbolic links are not accepted in plugin archives: {info.filename}")
        archive.extractall(target_dir)


def _extract_7z_safely(archive_path: Path, target_dir: Path) -> None:
    try:
        import py7zr
    except ImportError as exc:
        raise RuntimeError("py7zr is required to install the portable MKVToolNix archive. Run setup_windows.ps1 again.") from exc

    target_dir.mkdir(parents=True, exist_ok=True)
    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        for member in archive.list():
            _safe_member_path(target_dir, member.filename)
            if getattr(member, "is_symlink", False):
                raise ValueError(f"Symbolic links are not accepted in plugin archives: {member.filename}")
        archive.extractall(path=target_dir)


def _atomic_replace_directory(staged_dir: Path, target_dir: Path) -> None:
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    previous = target_dir.with_name(f".{target_dir.name}.previous-{uuid.uuid4().hex[:8]}")
    had_previous = target_dir.exists()
    if had_previous:
        target_dir.replace(previous)
    try:
        staged_dir.replace(target_dir)
    except Exception:
        if had_previous and previous.exists() and not target_dir.exists():
            previous.replace(target_dir)
        raise
    if previous.exists():
        shutil.rmtree(previous, ignore_errors=True)


def _resolve_ffmpeg_release() -> tuple[str, str, str]:
    response = requests.get(FFMPEG_RELEASE_API, timeout=(15, 30))
    response.raise_for_status()
    release = response.json()
    assets = {item.get("name"): item for item in release.get("assets", [])}
    asset = assets.get(FFMPEG_ASSET_NAME)
    if not asset:
        raise RuntimeError(f"The official FFmpeg release does not contain {FFMPEG_ASSET_NAME}.")
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:") or len(digest) != len("sha256:") + 64:
        raise RuntimeError("GitHub did not publish a SHA-256 digest for the FFmpeg asset; installation was stopped.")
    url = str(asset.get("browser_download_url") or "")
    if not url.startswith("https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/"):
        raise RuntimeError("The FFmpeg asset URL did not match the expected official release host.")
    release_label = str(release.get("published_at") or release.get("tag_name") or "latest")
    return url, digest.split(":", 1)[1].lower(), release_label


def _install_ffmpeg(plugins_root: Path) -> dict[str, str]:
    staging_root = plugins_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ffmpeg-", dir=staging_root) as temp_name:
        temp = Path(temp_name)
        archive = temp / FFMPEG_ASSET_NAME
        extract_dir = temp / "extract"
        install_dir = temp / "FFMPEG"

        url, expected_hash, release_label = _resolve_ffmpeg_release()
        logger.info("Downloading FFmpeg build published %s.", release_label)
        _download(url, archive)
        actual_hash = _sha256(archive)
        if actual_hash != expected_hash:
            raise RuntimeError(f"FFmpeg SHA-256 mismatch: expected {expected_hash}, received {actual_hash}.")

        _extract_zip_safely(archive, extract_dir)
        ffmpeg_matches = list(extract_dir.rglob("ffmpeg.exe"))
        if not ffmpeg_matches:
            raise RuntimeError("The FFmpeg archive did not contain ffmpeg.exe.")
        source_bin = ffmpeg_matches[0].parent
        if not (source_bin / "ffprobe.exe").is_file():
            raise RuntimeError("The FFmpeg archive did not contain ffprobe.exe beside ffmpeg.exe.")
        shutil.copytree(source_bin, install_dir / "bin")

        for name in ("ffmpeg", "ffprobe"):
            ok, detail = _version_ok(install_dir / "bin" / f"{name}.exe")
            if not ok:
                raise RuntimeError(f"Downloaded {name}.exe did not pass its version check: {detail}")
        _atomic_replace_directory(install_dir, plugins_root / "FFMPEG")
        return {
            "release": release_label,
            "sha256": actual_hash,
            "ffmpeg": str(plugins_root / "FFMPEG" / "bin" / "ffmpeg.exe"),
            "ffprobe": str(plugins_root / "FFMPEG" / "bin" / "ffprobe.exe"),
        }


def _install_mkvtoolnix(plugins_root: Path) -> dict[str, str]:
    staging_root = plugins_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mkvtoolnix-", dir=staging_root) as temp_name:
        temp = Path(temp_name)
        archive = temp / MKVTOOLNIX_ARCHIVE
        extract_dir = temp / "extract"
        install_dir = temp / "MKVToolNix"

        logger.info("Downloading official portable MKVToolNix %s.", MKVTOOLNIX_VERSION)
        _download(MKVTOOLNIX_URL, archive)
        actual_hash = _sha256(archive)
        if actual_hash != MKVTOOLNIX_SHA256:
            raise RuntimeError(
                f"MKVToolNix SHA-256 mismatch: expected {MKVTOOLNIX_SHA256}, received {actual_hash}."
            )

        _extract_7z_safely(archive, extract_dir)
        merge_matches = list(extract_dir.rglob("mkvmerge.exe"))
        propedit_matches = list(extract_dir.rglob("mkvpropedit.exe"))
        if not merge_matches or not propedit_matches:
            raise RuntimeError("The MKVToolNix archive is missing mkvmerge.exe or mkvpropedit.exe.")
        merge_dir = merge_matches[0].parent
        if not (merge_dir / "mkvpropedit.exe").is_file():
            raise RuntimeError("mkvmerge.exe and mkvpropedit.exe were not found in the same portable directory.")
        shutil.copytree(merge_dir, install_dir)

        for name in ("mkvmerge", "mkvpropedit"):
            ok, detail = _version_ok(install_dir / f"{name}.exe", ("--version",))
            if not ok:
                raise RuntimeError(f"Downloaded {name}.exe did not pass its version check: {detail}")
        _atomic_replace_directory(install_dir, plugins_root / "MKVToolNix")
        return {
            "version": MKVTOOLNIX_VERSION,
            "sha256": actual_hash,
            "mkvmerge": str(plugins_root / "MKVToolNix" / "mkvmerge.exe"),
            "mkvpropedit": str(plugins_root / "MKVToolNix" / "mkvpropedit.exe"),
        }


def _inspect_local_tools(plugins_root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name in ("ffmpeg", "ffprobe", "mkvmerge", "mkvpropedit"):
        path = find_local_tool(plugins_root, name)
        args = ("--version",) if name.startswith("mkv") else ("-version",)
        ok, version = _version_ok(path, args) if path else (False, None)
        result[name] = {"available": ok, "path": str(path) if path else None, "version": version}
    return result


def _clean_stale_staging(plugins_root: Path) -> None:
    staging_root = plugins_root / ".staging"
    if not staging_root.is_dir():
        return
    for item in staging_root.iterdir():
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove stale plugin staging item: %s", item)


def ensure_required_plugins(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Prepare the local Windows multimedia tools automatically at backend startup."""
    global _STATE
    cfg = config or load_config()
    plugins_root = Path(cfg["plugins_path"]).expanduser().resolve()
    with _INSTALL_LOCK:
        with _STATE_LOCK:
            _STATE = {"status": "checking", "last_error": None, "last_check": None, "installed": {}}

        if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
            tools = _inspect_local_tools(plugins_root)
            with _STATE_LOCK:
                _STATE = {
                    "status": "ready" if all(item["available"] for item in tools.values()) else "manual_setup_required",
                    "last_error": None,
                    "last_check": None,
                    "installed": tools,
                    "message": "Automatic downloads are enabled for Windows x64; other platforms must provide the tools through plugins_path or PATH.",
                }
                return dict(_STATE)

        try:
            plugins_root.mkdir(parents=True, exist_ok=True)
            _clean_stale_staging(plugins_root)
            tools = _inspect_local_tools(plugins_root)
            if not tools["ffmpeg"]["available"] or not tools["ffprobe"]["available"]:
                _install_ffmpeg(plugins_root)
            if not tools["mkvmerge"]["available"] or not tools["mkvpropedit"]["available"]:
                _install_mkvtoolnix(plugins_root)

            tools = _inspect_local_tools(plugins_root)
            missing = [name for name, info in tools.items() if not info["available"]]
            if missing:
                raise RuntimeError("The following tools failed their post-install check: " + ", ".join(missing))
            manifest_path = plugins_root / "plugins_manifest.json"
            manifest_tmp = manifest_path.with_suffix(".tmp")
            manifest_tmp.write_text(json.dumps({"tools": tools}, indent=2, ensure_ascii=False), encoding="utf-8")
            manifest_tmp.replace(manifest_path)
            with _STATE_LOCK:
                _STATE = {
                    "status": "ready",
                    "last_error": None,
                    "last_check": None,
                    "installed": tools,
                    "message": "All required multimedia tools are installed and verified.",
                }
        except Exception as exc:
            logger.exception("Automatic multimedia tool setup failed.")
            with _STATE_LOCK:
                _STATE = {
                    "status": "error",
                    "last_error": str(exc),
                    "last_check": None,
                    "installed": _inspect_local_tools(plugins_root),
                    "message": "Automatic setup could not complete. The backend remains available; analysis or injection will require the missing tool.",
                }
        return dict(_STATE)


def plugin_manager_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    plugins_root = Path(cfg["plugins_path"]).expanduser().resolve()
    local_tools = _inspect_local_tools(plugins_root)
    with _STATE_LOCK:
        state = dict(_STATE)
    state["plugins_path"] = str(plugins_root)
    state["installed"] = local_tools
    return state
