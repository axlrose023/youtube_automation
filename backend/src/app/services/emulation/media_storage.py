from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class MediaStorage(Protocol):
    async def remove_capture_dir(self, capture_dir: str) -> None: ...


class LocalMediaStorage:
    def __init__(self, base_path: Path) -> None:
        self._base_path = base_path

    async def remove_capture_dir(self, capture_dir: str) -> None:
        full_path = self._base_path / capture_dir
        if not full_path.exists():
            return
        try:
            shutil.rmtree(full_path)
            logger.info("Cleaned up media: %s", capture_dir)
        except Exception:
            logger.warning("Failed to cleanup media: %s", capture_dir)
