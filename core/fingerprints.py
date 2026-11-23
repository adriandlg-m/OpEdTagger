from __future__ import annotations

import hashlib
from pathlib import Path

ANALYZER_VERSION = "chroma-template-v1"
_SAMPLE_SIZE = 1024 * 1024


def file_fingerprint(path: Path) -> str:
    """Return a sampled SHA-256 fingerprint plus stable file metadata.

    For large MKVs this avoids reading several gigabytes on every cache lookup,
    while detecting ordinary replacements and edits near either file boundary.
    It is a cache identity hint, not a forensic full-file checksum.
    """
    before = path.stat()
    digest = hashlib.sha256()
    digest.update(f"{before.st_size}:{before.st_mtime_ns}".encode())
    with path.open("rb") as stream:
        digest.update(stream.read(_SAMPLE_SIZE))
        if before.st_size > _SAMPLE_SIZE:
            stream.seek(max(0, before.st_size - _SAMPLE_SIZE))
            digest.update(stream.read(_SAMPLE_SIZE))
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"File changed while its fingerprint was being calculated: {path.name}")
    return digest.hexdigest()


def templates_signature(files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.name.lower()):
        digest.update(path.name.encode("utf-8", errors="replace"))
        digest.update(file_fingerprint(path).encode())
    return digest.hexdigest()
