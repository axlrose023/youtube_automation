from __future__ import annotations

from pathlib import Path
from typing import Protocol


class AdCaptureProviderFactory(Protocol):
    def create(self, context: object, base_path: Path) -> object: ...


class DefaultAdCaptureProviderFactory:
    def create(self, context: object, base_path: Path) -> object:
        from app.services.emulation.browser.ads.capture import AdCreativeCapture

        return AdCreativeCapture(context, base_path)
