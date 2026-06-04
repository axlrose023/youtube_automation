from __future__ import annotations

import os
import shutil
from pathlib import Path


_HOMEBREW_ANDROID_SDK_ROOT = Path("/opt/homebrew/share/android-commandlinetools")
_HOMEBREW_ANDROID_PLATFORM_TOOLS_ROOT = Path("/opt/homebrew/Caskroom/android-platform-tools")
_HOMEBREW_APPIUM_BIN = Path("/opt/homebrew/bin/appium")
_HOMEBREW_JAVA_HOME = Path("/opt/homebrew/opt/openjdk")


def resolve_tool_path(tool_name: str) -> str | None:
    direct_match = shutil.which(tool_name)
    if direct_match:
        return direct_match

    for candidate in _iter_tool_candidates(tool_name):
        if candidate.is_file():
            return str(candidate)

    return None


def require_tool_path(tool_name: str) -> str:
    resolved = resolve_tool_path(tool_name)
    if resolved:
        return resolved
    raise FileNotFoundError(f"Required tool is missing: {tool_name}")


def build_android_runtime_env() -> dict[str, str]:
    env = dict(os.environ)
    sdk_root = ensure_sdk_root_layout()
    env["ANDROID_SDK_ROOT"] = str(sdk_root)
    env["ANDROID_HOME"] = str(sdk_root)
    env["ADB_VENDOR_KEYS"] = os.environ.get("ADB_VENDOR_KEYS") or str(Path.home() / ".android")

    java_home = os.environ.get("JAVA_HOME") or str(_HOMEBREW_JAVA_HOME)
    env["JAVA_HOME"] = java_home

    path_entries = [
        str(Path(java_home) / "bin"),
        str(sdk_root / "emulator"),
        str(sdk_root / "cmdline-tools" / "latest" / "bin"),
        str(sdk_root / "platform-tools"),
    ]
    build_tools_dir = _find_latest_build_tools_dir(sdk_root)
    if build_tools_dir is not None:
        path_entries.append(str(build_tools_dir))
    existing_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([*path_entries, existing_path])
    return env


def ensure_sdk_root_layout() -> Path:
    sdk_root = infer_sdk_root()
    platform_tools_dir = sdk_root / "platform-tools"
    if platform_tools_dir.exists():
        return sdk_root

    homebrew_platform_tools = _find_homebrew_platform_tools_dir()
    if homebrew_platform_tools is None:
        return sdk_root

    platform_tools_dir.symlink_to(homebrew_platform_tools)
    return sdk_root


def infer_sdk_root() -> Path:
    roots = _collect_sdk_roots()
    if roots:
        return roots[0]
    return _HOMEBREW_ANDROID_SDK_ROOT


def _iter_tool_candidates(tool_name: str) -> list[Path]:
    candidates: list[Path] = []

    if tool_name == "appium":
        candidates.append(_HOMEBREW_APPIUM_BIN)

    sdk_roots = _collect_sdk_roots()
    for sdk_root in sdk_roots:
        candidates.extend(_sdk_tool_candidates(sdk_root, tool_name))

    if tool_name == "adb" and _HOMEBREW_ANDROID_PLATFORM_TOOLS_ROOT.exists():
        candidates.extend(
            sorted(_HOMEBREW_ANDROID_PLATFORM_TOOLS_ROOT.glob("*/platform-tools/adb"))
        )

    return candidates


def _collect_sdk_roots() -> list[Path]:
    roots: list[Path] = []
    for env_name in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        value = os.environ.get(env_name)
        if value:
            roots.append(Path(value))
    roots.append(_HOMEBREW_ANDROID_SDK_ROOT)
    return _unique_paths(roots)


def _find_homebrew_platform_tools_dir() -> Path | None:
    if not _HOMEBREW_ANDROID_PLATFORM_TOOLS_ROOT.exists():
        return None
    matches = sorted(_HOMEBREW_ANDROID_PLATFORM_TOOLS_ROOT.glob("*/platform-tools"))
    if not matches:
        return None
    return matches[-1]


def _sdk_tool_candidates(sdk_root: Path, tool_name: str) -> list[Path]:
    if tool_name == "emulator":
        return [sdk_root / "emulator" / "emulator"]
    if tool_name == "adb":
        return [sdk_root / "platform-tools" / "adb"]
    if tool_name == "aapt2":
        build_tools_dir = _find_latest_build_tools_dir(sdk_root)
        return [build_tools_dir / "aapt2"] if build_tools_dir is not None else []
    if tool_name in {"sdkmanager", "avdmanager"}:
        return [sdk_root / "cmdline-tools" / "latest" / "bin" / tool_name]
    return []


def _find_latest_build_tools_dir(sdk_root: Path) -> Path | None:
    build_tools_root = sdk_root / "build-tools"
    if not build_tools_root.exists():
        return None
    candidates = [path for path in build_tools_root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.name)[-1]


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        normalized = path.expanduser()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique
