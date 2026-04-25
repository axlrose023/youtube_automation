import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.common.auth import AuthenticateMainRoles

router = APIRouter(dependencies=[Depends(AuthenticateMainRoles())])

def _find_script() -> Path:
    # Env override
    if env := os.environ.get("YTA_ANDROID_UI_SCRIPT"):
        return Path(env)
    # Docker: ops/ mounted at /app/ops
    docker_path = Path("/app/ops/android-ui.sh")
    if docker_path.exists():
        return docker_path
    # Local dev: derive from source tree
    return Path(__file__).resolve().parents[6] / "ops" / "android-ui.sh"

_ANDROID_UI_SCRIPT = _find_script()


class AndroidUiStartResponse(BaseModel):
    novnc_url: str
    status: str


class AndroidUiStatusResponse(BaseModel):
    status: str


async def _run_script(*args: str, timeout: float = 120.0) -> str:
    if not _ANDROID_UI_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="android-ui.sh not found")
    cmd = [str(_ANDROID_UI_SCRIPT), *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(errors="replace")
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="android-ui.sh timed out")


def _extract_novnc_url(output: str) -> str | None:
    m = re.search(r"noVNC:\s*(\S+)", output)
    return m.group(1) if m else None


@router.post("/android-ui/start", response_model=AndroidUiStartResponse)
async def start_android_ui() -> AndroidUiStartResponse:
    output = await _run_script("start", timeout=240.0)
    # Extract URL from script output, fall back to env-derived direct URL
    import re
    m = re.search(r"noVNC:\s*(\S+)", output)
    if m:
        novnc_url = m.group(1)
    else:
        public_url = os.environ.get("YTA_PUBLIC_URL", "").rstrip("/")
        novnc_port = os.environ.get("YTA_ANDROID_BOOTSTRAP_NOVNC_PORT", "6080")
        if public_url:
            novnc_url = f"{public_url}:{novnc_port}/vnc.html"
        else:
            novnc_url = f"http://localhost:{novnc_port}/vnc.html"
    return AndroidUiStartResponse(novnc_url=novnc_url, status="started")


@router.post("/android-ui/save-and-stop", response_model=AndroidUiStatusResponse)
async def save_and_stop_android_ui() -> AndroidUiStatusResponse:
    await _run_script("save-snapshot", timeout=120.0)
    await _run_script("stop", timeout=60.0)
    return AndroidUiStatusResponse(status="stopped")
