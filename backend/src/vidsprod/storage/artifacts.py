"""Artifact storage: screenshots, frames, clips, exports, reports.

Files live under ARTIFACT_DIR/<job>/<kind>/<id><ext>; the database row records the relative
path, size, SHA-256 and producer. Reads resolve the stored relative path strictly inside
the root, so a crafted row cannot make the API serve an arbitrary file.
"""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from vidsprod.db.ids import uuid7
from vidsprod.db.models import Artifact
from vidsprod.domain.enums import ArtifactKind
from vidsprod.errors import NotFoundError


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def work_dir(self, job_id: uuid.UUID | None, *parts: str) -> Path:
        path = self.root / "work" / (str(job_id) if job_id else "global")
        for part in parts:
            path = path / part
        path.mkdir(parents=True, exist_ok=True)
        return path

    def resolve(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root):
            raise NotFoundError("artifact path escapes the store")
        if not candidate.is_file():
            raise NotFoundError("artifact file is missing")
        return candidate

    async def put_file(
        self, session: AsyncSession, source: Path, *, job_id: uuid.UUID | None,
        kind: ArtifactKind, producer: str, simulated: bool = False, mime: str | None = None,
        meta: dict[str, Any] | None = None, move: bool = False,
    ) -> Artifact:
        artifact_id = uuid7()
        suffix = source.suffix.lower() or ".bin"
        relative = Path(str(job_id) if job_id else "global") / kind.value.lower() / f"{artifact_id}{suffix}"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(source), target)
        else:
            shutil.copyfile(source, target)
        artifact = Artifact(
            id=artifact_id, job_id=job_id, kind=kind.value, uri=str(relative),
            sha256=sha256_file(target), size_bytes=target.stat().st_size,
            mime=mime or mimetypes.guess_type(target.name)[0] or "application/octet-stream",
            producer=producer, simulated=simulated, meta=meta or {},
        )
        session.add(artifact)
        await session.flush()
        return artifact

    async def put_bytes(
        self, session: AsyncSession, data: bytes, suffix: str, *, job_id: uuid.UUID | None,
        kind: ArtifactKind, producer: str, simulated: bool = False, mime: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Artifact:
        tmp = self.work_dir(job_id, "tmp") / f"{uuid7()}{suffix}"
        tmp.write_bytes(data)
        return await self.put_file(session, tmp, job_id=job_id, kind=kind, producer=producer,
                                   simulated=simulated, mime=mime, meta=meta, move=True)
