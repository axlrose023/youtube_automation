from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import google.generativeai as genai

logger = logging.getLogger(__name__)

_UPLOAD_POLL_INTERVAL_S = 0.5
_UPLOAD_TIMEOUT_S = 60
_GENERATE_TIMEOUT_S = 30


class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(model)

    async def generate_from_video(self, video_path: Path, prompt: str) -> str:
        uploaded = await asyncio.to_thread(
            genai.upload_file, str(video_path), mime_type="video/webm",
        )
        try:
            await self._wait_for_processing(uploaded)

            response = await asyncio.wait_for(
                asyncio.to_thread(self._model.generate_content, [uploaded, prompt]),
                timeout=_GENERATE_TIMEOUT_S,
            )
            return response.text
        finally:
            try:
                await asyncio.to_thread(genai.delete_file, uploaded.name)
            except Exception:
                logger.warning("Failed to cleanup Gemini file %s", uploaded.name)

    async def _wait_for_processing(self, uploaded) -> None:
        elapsed = 0.0
        while uploaded.state.name == "PROCESSING":
            if elapsed >= _UPLOAD_TIMEOUT_S:
                raise TimeoutError(
                    f"Gemini file processing timed out after {_UPLOAD_TIMEOUT_S}s",
                )
            await asyncio.sleep(_UPLOAD_POLL_INTERVAL_S)
            elapsed += _UPLOAD_POLL_INTERVAL_S
            uploaded = await asyncio.to_thread(genai.get_file, uploaded.name)

        if uploaded.state.name == "FAILED":
            raise RuntimeError(f"Gemini file processing failed: {uploaded.name}")
