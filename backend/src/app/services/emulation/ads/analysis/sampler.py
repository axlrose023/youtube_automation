from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_WHOLE_VIDEO_MAX_SECONDS = 30.0
_HEAD_SEGMENT_SECONDS = 20.0
_TAIL_SEGMENT_SECONDS = 10.0
_FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class PreparedAnalysisVideo:
    path: Path
    cleanup_dir: Path | None = None
    sampled: bool = False
    duration_seconds: float | None = None

    async def cleanup(self) -> None:
        if self.cleanup_dir is None:
            return
        await asyncio.to_thread(shutil.rmtree, self.cleanup_dir, ignore_errors=True)


class AdAnalysisVideoSampler:
    def __init__(
        self,
        *,
        ffmpeg_bin: str | None = None,
        ffprobe_bin: str | None = None,
    ) -> None:
        self._ffmpeg_bin = ffmpeg_bin or shutil.which("ffmpeg")
        self._ffprobe_bin = ffprobe_bin or shutil.which("ffprobe")

    async def prepare(self, video_path: Path) -> PreparedAnalysisVideo:
        duration = await self._probe_duration(video_path)
        if duration is None:
            return PreparedAnalysisVideo(path=video_path)

        if duration <= _WHOLE_VIDEO_MAX_SECONDS:
            return PreparedAnalysisVideo(
                path=video_path,
                duration_seconds=duration,
            )

        if not self._ffmpeg_bin or not self._ffprobe_bin:
            logger.warning(
                "Video sampler unavailable for %s (ffmpeg=%s ffprobe=%s), using full clip",
                video_path,
                bool(self._ffmpeg_bin),
                bool(self._ffprobe_bin),
            )
            return PreparedAnalysisVideo(
                path=video_path,
                duration_seconds=duration,
            )

        output = await self._build_head_tail_sample(video_path, duration)
        if output is None:
            return PreparedAnalysisVideo(
                path=video_path,
                duration_seconds=duration,
            )
        return output

    async def prepare_focus_window(
        self,
        video_path: Path,
        *,
        start_seconds: float,
        max_duration_seconds: float,
    ) -> PreparedAnalysisVideo:
        duration = await self._probe_duration(video_path)
        if duration is None:
            return await self.prepare(video_path)

        start = max(0.0, min(float(start_seconds), max(duration - 0.25, 0.0)))
        clip_duration = min(float(max_duration_seconds), max(duration - start, 0.0))
        if clip_duration <= 0:
            return PreparedAnalysisVideo(
                path=video_path,
                duration_seconds=duration,
            )

        if start <= 0.05 and duration <= clip_duration + 0.5:
            return PreparedAnalysisVideo(
                path=video_path,
                duration_seconds=duration,
            )

        if not self._ffmpeg_bin:
            logger.warning(
                "Focus-window sampler unavailable for %s (ffmpeg missing), using full clip",
                video_path,
            )
            return PreparedAnalysisVideo(
                path=video_path,
                duration_seconds=duration,
            )

        output = await self._build_trimmed_sample(
            video_path,
            start_seconds=start,
            duration_seconds=clip_duration,
        )
        if output is None:
            return PreparedAnalysisVideo(
                path=video_path,
                duration_seconds=duration,
            )
        return output

    async def _probe_duration(self, video_path: Path) -> float | None:
        if not self._ffprobe_bin:
            return None

        process = await asyncio.create_subprocess_exec(
            self._ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.warning(
                "ffprobe failed for %s: %s",
                video_path,
                stderr.decode("utf-8", errors="ignore").strip(),
            )
            return await self._probe_duration_with_ffmpeg(video_path)

        try:
            payload = json.loads(stdout.decode("utf-8"))
            duration = self._extract_duration(payload)
        except (ValueError, json.JSONDecodeError, AttributeError, TypeError):
            logger.warning("Unable to parse ffprobe duration for %s", video_path)
            duration = None

        if duration is not None and duration > 0:
            return duration
        return await self._probe_duration_with_ffmpeg(video_path)

    async def _probe_duration_with_ffmpeg(self, video_path: Path) -> float | None:
        if not self._ffmpeg_bin:
            return None

        process = await asyncio.create_subprocess_exec(
            self._ffmpeg_bin,
            "-i",
            str(video_path),
            "-f",
            "null",
            "-",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        stderr_text = stderr.decode("utf-8", errors="ignore")
        matches = _FFMPEG_TIME_RE.findall(stderr_text)
        if not matches:
            logger.warning("Unable to infer duration from ffmpeg output for %s", video_path)
            return None

        hours, minutes, seconds = matches[-1]
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    async def _build_head_tail_sample(
        self,
        video_path: Path,
        duration: float,
    ) -> PreparedAnalysisVideo | None:
        if not self._ffmpeg_bin:
            return None

        head_seconds = min(_HEAD_SEGMENT_SECONDS, duration)
        tail_seconds = min(_TAIL_SEGMENT_SECONDS, max(duration - head_seconds, 0.0))
        tail_start = max(duration - tail_seconds, head_seconds)

        if duration <= (head_seconds + tail_seconds + 0.5):
            return PreparedAnalysisVideo(
                path=video_path,
                duration_seconds=duration,
            )

        temp_dir = Path(tempfile.mkdtemp(prefix="ad-analysis-", suffix="-sample"))
        output_path = temp_dir / "analysis_sample.mp4"
        has_audio = await self._has_audio_stream(video_path)
        if has_audio:
            filter_complex = ";".join(
                [
                    f"[0:v]trim=start=0:end={head_seconds},setpts=PTS-STARTPTS[v0]",
                    f"[0:v]trim=start={tail_start}:end={duration},setpts=PTS-STARTPTS[v1]",
                    f"[0:a]atrim=start=0:end={head_seconds},asetpts=PTS-STARTPTS[a0]",
                    f"[0:a]atrim=start={tail_start}:end={duration},asetpts=PTS-STARTPTS[a1]",
                    "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
                ]
            )
            map_args = ["-map", "[v]", "-map", "[a]", "-c:a", "aac"]
        else:
            filter_complex = ";".join(
                [
                    f"[0:v]trim=start=0:end={head_seconds},setpts=PTS-STARTPTS[v0]",
                    f"[0:v]trim=start={tail_start}:end={duration},setpts=PTS-STARTPTS[v1]",
                    "[v0][v1]concat=n=2:v=1:a=0[v]",
                ]
            )
            map_args = ["-map", "[v]"]

        process = await asyncio.create_subprocess_exec(
            self._ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-filter_complex",
            filter_complex,
            *map_args,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-movflags",
            "+faststart",
            str(output_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0 or not output_path.exists():
            logger.warning(
                "ffmpeg sample build failed for %s: %s",
                video_path,
                stderr.decode("utf-8", errors="ignore").strip(),
            )
            await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)
            return None

        return PreparedAnalysisVideo(
            path=output_path,
            cleanup_dir=temp_dir,
            sampled=True,
            duration_seconds=head_seconds + tail_seconds,
        )

    async def _build_trimmed_sample(
        self,
        video_path: Path,
        *,
        start_seconds: float,
        duration_seconds: float,
    ) -> PreparedAnalysisVideo | None:
        if not self._ffmpeg_bin:
            return None

        temp_dir = Path(tempfile.mkdtemp(prefix="ad-analysis-", suffix="-focus"))
        output_path = temp_dir / "analysis_focus.mp4"
        has_audio = await self._has_audio_stream(video_path)
        audio_args = ["-c:a", "aac"] if has_audio else ["-an"]

        process = await asyncio.create_subprocess_exec(
            self._ffmpeg_bin,
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-t",
            f"{duration_seconds:.3f}",
            "-i",
            str(video_path),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            *audio_args,
            "-movflags",
            "+faststart",
            str(output_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0 or not output_path.exists():
            logger.warning(
                "ffmpeg focus sample build failed for %s: %s",
                video_path,
                stderr.decode("utf-8", errors="ignore").strip(),
            )
            await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)
            return None

        return PreparedAnalysisVideo(
            path=output_path,
            cleanup_dir=temp_dir,
            sampled=True,
            duration_seconds=duration_seconds,
        )

    async def _has_audio_stream(self, video_path: Path) -> bool:
        if not self._ffprobe_bin:
            return False

        process = await asyncio.create_subprocess_exec(
            self._ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "json",
            str(video_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return False
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return False
        return bool(payload.get("streams"))

    @staticmethod
    def _extract_duration(payload: dict) -> float | None:
        candidates: list[float] = []

        format_duration = payload.get("format", {}).get("duration")
        if format_duration is not None:
            try:
                candidates.append(float(format_duration))
            except (TypeError, ValueError):
                pass

        for stream in payload.get("streams", []) or []:
            if not isinstance(stream, dict):
                continue
            stream_duration = stream.get("duration")
            if stream_duration is None:
                continue
            try:
                candidates.append(float(stream_duration))
            except (TypeError, ValueError):
                continue

        if not candidates:
            return None
        positive = [value for value in candidates if value > 0]
        if not positive:
            return None
        return max(positive)
