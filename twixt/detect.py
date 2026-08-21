"""Discovers everything the launcher needs to know about this machine and the
file being played: where the runtime lives, how fast the panel refreshes, and
what the source actually is.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"

_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


# --- tool discovery ---------------------------------------------------------

def _find(name: str, *bundled: Path) -> Path | None:
    """Prefer the copy we installed into runtime/, fall back to PATH."""
    for candidate in bundled:
        if candidate.is_file():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def mpv_path() -> Path | None:
    return _find("mpv", RUNTIME / "mpv" / "mpv.exe", RUNTIME / "mpv" / "mpv")


def ffprobe_path() -> Path | None:
    return _find("ffprobe", RUNTIME / "ffmpeg" / "ffprobe.exe")


def vs_package_dir() -> Path:
    """Where the VapourSynth binaries land once the wheel is installed.

    R79 dropped the loose-DLL portable layout in favour of a wheel, so
    vsscript.dll / vspipe.exe now live inside the installed package.
    """
    return RUNTIME / "vapoursynth" / "Lib" / "site-packages" / "vapoursynth"


def vapoursynth_dir() -> Path | None:
    """The runtime root, or None when VapourSynth is not usable yet."""
    base = RUNTIME / "vapoursynth"
    pkg = vs_package_dir()
    if any((pkg / n).is_file() for n in ("vsscript.dll", "VSScript.dll")):
        return base
    # Pre-R79 layout, kept so an older install still works.
    if any((base / n).is_file() for n in ("VSScript.dll", "vapoursynth.dll")):
        return base
    return None


def vspipe_path() -> Path | None:
    for candidate in (vs_package_dir() / "vspipe.exe",
                      RUNTIME / "vapoursynth" / "vspipe.exe"):
        if candidate.is_file():
            return candidate
    found = shutil.which("vspipe")
    return Path(found) if found else None


def vs_plugins_dir() -> Path:
    """Primary plugin directory.

    VapourSynth portable autoloads `vapoursynth64/plugins`, so that is where
    vsrepo and the bootstrap both put things.
    """
    return RUNTIME / "vapoursynth" / "vapoursynth64" / "plugins"


def plugin_dirs() -> list[Path]:
    """Every directory that may hold plugins or importable helper scripts."""
    base = RUNTIME / "vapoursynth"
    return [d for d in (
        base / "vapoursynth64" / "plugins",
        base / "vapoursynth64" / "scripts",
        base / "vs-plugins",
    ) if d.is_dir()]


def installed_plugins() -> set[str]:
    """Which VapourSynth plugins and helper scripts are present, by stem."""
    found: set[str] = set()
    for d in plugin_dirs():
        found |= {p.stem.lower() for p in d.glob("*.dll")}
        found |= {p.stem.lower() for p in d.glob("*.py")}
    return found


def available_backends() -> dict[str, bool]:
    """Which interpolation backends this machine can currently run."""
    plugins = installed_plugins()

    def has(*names: str) -> bool:
        return any(any(n in p for p in plugins) for n in names)

    return {
        # vsmlrt.py drives TensorRT; the ncnn rife plugin is the fallback.
        "rife": has("vsmlrt", "vstrt", "rife"),
        "mvtools": has("mvtools"),
        "svpflow": has("svpflow"),
    }


# --- display ----------------------------------------------------------------

def display_refresh_hz(default: float = 60.0) -> float:
    """Panel refresh rate. This is the interpolation target in `display` mode."""
    if os.name != "nt":
        return default
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController |"
             " Where-Object CurrentRefreshRate -gt 0 |"
             " Select-Object -First 1).CurrentRefreshRate"],
            capture_output=True, text=True, timeout=15, **_NO_WINDOW,
        )
        value = float(out.stdout.strip())
        return value if value > 0 else default
    except Exception:
        return default


# --- source probing ---------------------------------------------------------

@dataclass
class SourceInfo:
    path: Path
    fps: float = 23.976
    width: int = 0
    height: int = 0
    matrix: str = "709"
    codec: str = ""
    duration: float = 0.0

    @property
    def megapixels(self) -> float:
        return (self.width * self.height) / 1_000_000 if self.width else 2.07


_MATRIX_MAP = {
    "bt709": "709", "bt470bg": "470bg", "smpte170m": "170m",
    "bt2020nc": "2020ncl", "bt2020_ncl": "2020ncl", "smpte240m": "240m",
    "fcc": "fcc", "gbr": "rgb",
}


def probe(path: str | Path) -> SourceInfo:
    """Read frame rate, size and colour matrix so the filter chain can be exact."""
    path = Path(path)
    info = SourceInfo(path=path)

    ffprobe = ffprobe_path()
    if not ffprobe:
        return info

    try:
        out = subprocess.run(
            [str(ffprobe), "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=r_frame_rate,avg_frame_rate,width,height,color_space,codec_name"
             ":format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, **_NO_WINDOW,
        )
        data = json.loads(out.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]

        # avg_frame_rate reflects the real average; r_frame_rate can report the
        # container tick rate (often 2x) for interlaced or VFR sources.
        for key in ("avg_frame_rate", "r_frame_rate"):
            raw = stream.get(key) or ""
            if "/" in raw:
                num, den = raw.split("/")
                if float(den) > 0 and float(num) > 0:
                    info.fps = float(num) / float(den)
                    break

        info.width = int(stream.get("width") or 0)
        info.height = int(stream.get("height") or 0)
        info.codec = stream.get("codec_name") or ""
        info.matrix = _MATRIX_MAP.get((stream.get("color_space") or "").lower(), "709")
        info.duration = float((data.get("format") or {}).get("duration") or 0.0)
    except Exception:
        pass

    return info


# --- environment for spawning the player ------------------------------------

def player_env() -> dict[str, str]:
    """Environment that points mpv and VapourSynth at our bundled runtime."""
    env = dict(os.environ)
    vs_dir = vapoursynth_dir()
    if not vs_dir:
        return env

    dirs = [str(d) for d in plugin_dirs()]
    site_packages = vs_package_dir().parent

    # The TensorRT and CUDA runtime DLLs sit next to the plugins, so they have
    # to be on PATH or vstrt.dll fails to load with an opaque error.
    env["PATH"] = os.pathsep.join(
        [str(vs_dir), str(vs_package_dir()), *dirs, env.get("PATH", "")]
    )

    # Autoload our plugin directory; the wheel layout has no portable.vs
    # convention to fall back on.
    env["VAPOURSYNTH_PLUGIN_PATH"] = str(vs_plugins_dir())

    # vsmlrt.py is imported by the generated script, and mpv's VSScript needs
    # to find the `vapoursynth` module itself.
    env["PYTHONPATH"] = os.pathsep.join(
        [str(site_packages), *dirs, env.get("PYTHONPATH", "")]
    )

    return env


def diagnose() -> list[tuple[str, bool, str]]:
    """Rows of (component, ok, detail) for `twixt doctor`."""
    mpv = mpv_path()
    ffp = ffprobe_path()
    vsd = vapoursynth_dir()
    backends = available_backends()

    rows = [
        ("mpv player", mpv is not None, str(mpv) if mpv else "not found - run bootstrap.ps1"),
        ("ffprobe", ffp is not None, str(ffp) if ffp else "not found - needed to read source fps"),
        ("VapourSynth", vsd is not None, str(vsd) if vsd else "not found - run bootstrap.ps1"),
        ("RIFE backend", backends["rife"], "vs-mlrt / rife plugin"),
        ("MVTools backend", backends["mvtools"], "mvtools plugin"),
        ("SVPflow backend", backends["svpflow"], "optional, only if you own SVP"),
        ("Display", True, f"{display_refresh_hz():.0f} Hz"),
        ("Python", sys.version_info >= (3, 10), sys.version.split()[0]),
    ]
    return rows
