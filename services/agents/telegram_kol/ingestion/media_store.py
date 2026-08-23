from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MediaArtifact:
    media_hash: str
    path: Path


class MediaStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes, *, suffix: str = "") -> MediaArtifact:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / f"{digest}{suffix}"
        if not path.exists():
            path.write_bytes(content)
        return MediaArtifact(digest, path)
