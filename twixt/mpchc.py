"""Wires interpolation into MPC-HC, the way SVP does it.

MPC-HC is a DirectShow player: it builds a chain of filters from source to
screen. Dropping a processing filter into that chain means every video the
player opens goes through it automatically -- no per-file commands, no launcher.

Three pieces:
  1. `vapoursynth_filter_64.ax`, a DirectShow filter that runs a VapourSynth
     script on the frames passing through it. Registering it is the one step
     that needs administrator rights.
  2. A generated script it points at, kept at a fixed path so it can be
     re-tuned later without touching any registration.
  3. MPC-HC's own "external filters" list, set to prefer that filter.
"""

from __future__ import annotations

import os
import subprocess
import winreg
from pathlib import Path

from . import avs, detect
from .profiles import Profile

ROOT = Path(__file__).resolve().parent.parent
FILTER_DIR = ROOT / "runtime" / "dsfilter"
# The AviSynth build, not the VapourSynth one: the VapourSynth script host
# has to start an embedded Python interpreter inside the player, which
# deadlocks on filter creation here. AviSynth needs no interpreter.
FILTER_AX = FILTER_DIR / "avisynth_filter_64.ax"
SCRIPT_PATH = ROOT / "runtime" / "mpchc.avs"

# The VapourSynth build of the filter shares the AviSynth build's settings key.
FILTER_KEY = r"Software\AviSynthFilter"

FILTER_NAME = "AviSynth Filter"
# If both builds are left set to "prefer", MPC-HC can insert each of them
# and interpolate twice.
CONFLICTING_FILTERS = ("VapourSynth Filter",)

MPC_KEYS = [
    r"Software\MPC-HC\MPC-HC",
    r"Software\MPC-BE\MPC-BE",
]


# --- discovery --------------------------------------------------------------

def find_mpc_hc() -> Path | None:
    """Locate mpc-hc64.exe, preferring whatever the registry records."""
    for key in MPC_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                exe = Path(winreg.QueryValueEx(k, "ExePath")[0])
                if exe.is_file():
                    return exe
        except OSError:
            pass

    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")):
        if not base:
            continue
        for name in ("MPC-HC\\mpc-hc64.exe", "MPC-BE\\mpc-be64.exe"):
            candidate = Path(base) / name
            if candidate.is_file():
                return candidate
    return None


# DirectShow's "filter" category. Registered filters are listed as Instance
# subkeys here, which is a few dozen entries -- far cheaper to walk than the
# tens of thousands of CLSIDs under HKCR\CLSID.
DSHOW_FILTER_CATEGORY = (
    r"CLSID\{083863F1-70DE-11d0-BD40-00A0C911CE86}\Instance"
)


def registered_filters() -> dict[str, str]:
    """Map friendly name -> CLSID for every registered DirectShow filter."""
    found: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, DSHOW_FILTER_CATEGORY) as cat:
            index = 0
            while True:
                try:
                    clsid = winreg.EnumKey(cat, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(cat, clsid) as entry:
                        found[winreg.QueryValueEx(entry, "FriendlyName")[0]] = clsid
                except OSError:
                    continue
    except OSError:
        pass
    return found


def filter_server_path(clsid: str) -> str | None:
    """Which .ax file backs a given CLSID."""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            rf"CLSID\{clsid}\InprocServer32") as key:
            return winreg.QueryValueEx(key, "")[0]
    except OSError:
        return None


def real_filter_clsid() -> str | None:
    """CLSID of the registered VapourSynth Filter, or None."""
    return registered_filters().get(FILTER_NAME)


def is_registered() -> bool:
    return real_filter_clsid() is not None


# --- installation steps -----------------------------------------------------

def register_filter() -> tuple[bool, str]:
    """Register the DirectShow filter. Requires elevation."""
    if not FILTER_AX.is_file():
        return False, f"{FILTER_AX.name} not found - run bootstrap.ps1 -Component dsfilter"

    proc = subprocess.run(
        ["regsvr32", "/s", str(FILTER_AX)],
        capture_output=True, text=True,
    )
    if proc.returncode == 0 and is_registered():
        return True, "registered"
    return False, (
        "regsvr32 failed (exit %d). This step needs administrator rights."
        % proc.returncode
    )


def register_filter_elevated(timeout: int = 120) -> tuple[bool, str]:
    """Register the filter, asking Windows for elevation.

    Registering a DirectShow filter writes to HKEY_CLASSES_ROOT, so it needs
    administrator rights. This raises the standard UAC prompt rather than
    trying to work around it.
    """
    if not FILTER_AX.is_file():
        return False, f"{FILTER_AX.name} not found - run bootstrap.ps1 -Component dsfilter"
    if is_registered():
        return True, "already registered"

    import ctypes
    import time

    SW_HIDE = 0
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "regsvr32.exe", f'/s "{FILTER_AX}"', None, SW_HIDE
    )
    if result <= 32:
        return False, (
            "elevation was declined or failed (code %d). "
            "Right-click install-mpchc.cmd and choose 'Run as administrator'."
            % result
        )

    # ShellExecuteW returns before regsvr32 finishes; poll for the result.
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_registered():
            return True, "registered"
        time.sleep(0.5)
    return False, "registration did not complete in time"


def set_script_path(script: Path) -> None:
    """Tell the filter which script to run."""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, FILTER_KEY) as key:
        # AvsFile is what the AviSynth build reads; ScriptFile is the
        # VapourSynth build's name. Setting both keeps either working.
        winreg.SetValueEx(key, "AvsFile", 0, winreg.REG_SZ, str(script))
        winreg.SetValueEx(key, "ScriptFile", 0, winreg.REG_SZ, str(script))


def get_script_path() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, FILTER_KEY) as key:
            return winreg.QueryValueEx(key, "AvsFile")[0]
    except OSError:
        return None


def ensure_runtime_on_path() -> list[str]:
    """Put the VapourSynth and Python DLLs where the filter can load them.

    The filter runs inside the player's process, so it can only find the
    frame server through the user's PATH. This edits the per-user PATH only;
    the machine-wide one is left alone.
    """
    needed = [
        str(detect.RUNTIME / "avisynth"),                   # AviSynth.dll
    ]

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except OSError:
            current, kind = "", winreg.REG_EXPAND_SZ

        entries = [e for e in current.split(os.pathsep) if e]
        added = [d for d in needed if d not in entries]
        if added:
            entries.extend(added)
            winreg.SetValueEx(key, "Path", 0, kind, os.pathsep.join(entries))
    return added


# MPC-HC identifies an external filter by its DirectShow moniker, not a plain
# CLSID, and carries the list of media types the filter may claim.
MEDIATYPE_VIDEO = "{73646976-0000-0010-8000-00AA00389B71}"
_SUBTYPES = [
    "{3231564E-0000-0010-8000-00AA00389B71}",   # NV12
    "{32315659-0000-0010-8000-00AA00389B71}",   # YV12
    "{30323449-0000-0010-8000-00AA00389B71}",   # I420
    "{56555949-0000-0010-8000-00AA00389B71}",   # IYUV
    "{30313050-0000-0010-8000-00AA00389B71}",   # P010
    "{36313050-0000-0010-8000-00AA00389B71}",   # P016
    "{32595559-0000-0010-8000-00AA00389B71}",   # YUY2
    "{59565955-0000-0010-8000-00AA00389B71}",   # UYVY
    "{E436EB7D-524F-11CE-9F53-0020AF0BA770}",   # RGB24
    "{E436EB7E-524F-11CE-9F53-0020AF0BA770}",   # RGB32
    "{30424752-0000-0010-8000-00AA00389B71}",   # RGB0
]


def moniker_for(clsid: str) -> str:
    return f"@device:sw:{{083863F1-70DE-11D0-BD40-00A0C911CE86}}\\{clsid}"


def _mpc_base() -> str:
    for key in MPC_KEYS:
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, key).Close()
            return key
        except OSError:
            continue
    raise RuntimeError("MPC-HC settings not found in the registry. "
                       "Run MPC-HC once, then try again.")


def _filters_key() -> str:
    """MPC-HC keeps 64-bit external filters under Filters\\x64."""
    base = _mpc_base()
    for suffix in (r"\Filters\x64", r"\Filters"):
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + suffix).Close()
            return base + suffix
        except OSError:
            continue
    return base + r"\Filters\x64"


def configure_mpc_external_filter(clsid: str) -> str:
    """Point MPC-HC's external filter list at our filter, set to 'prefer'.

    If SVP already configured its own filter here, that slot is reused rather
    than adding a second one -- two preferred processing filters in the same
    graph would interpolate twice.
    """
    filters_key = _filters_key()

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, filters_key) as parent:
        target_slot = None
        reused = False

        index = 0
        while True:
            try:
                slot = winreg.EnumKey(parent, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(parent, slot) as existing:
                    name = winreg.QueryValueEx(existing, "Name")[0]
            except OSError:
                continue
            if name == FILTER_NAME:
                target_slot = slot
                break
            if name in CONFLICTING_FILTERS and target_slot is None:
                target_slot, reused = slot, True

        if target_slot is None:
            target_slot = f"{index:04d}"

        _write_filter_entry(parent, target_slot, clsid, fresh=not reused)

    return f"{filters_key}\\{target_slot}" + ("  (reused SVP's slot)" if reused else "")


def _write_filter_entry(parent, slot: str, clsid: str, fresh: bool) -> None:
    with winreg.CreateKey(parent, slot) as entry:
        winreg.SetValueEx(entry, "Name", 0, winreg.REG_SZ, FILTER_NAME)
        winreg.SetValueEx(entry, "DisplayName", 0, winreg.REG_SZ, moniker_for(clsid))
        winreg.SetValueEx(entry, "Enabled", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(entry, "SourceType", 0, winreg.REG_DWORD, 0)
        # LoadType 0 = "Prefer", which is what actually gets the filter inserted
        # into the graph. Merit is only consulted for LoadType 2.
        winreg.SetValueEx(entry, "LoadType", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(entry, "Merit", 0, winreg.REG_DWORD, 0x00200001)

        if fresh:
            # Declare the formats the filter may claim. Reused slots already
            # carry a working list, so leave those untouched.
            for i, subtype in enumerate(_SUBTYPES):
                winreg.SetValueEx(entry, f"org{i * 2:04d}", 0, winreg.REG_SZ,
                                  MEDIATYPE_VIDEO)
                winreg.SetValueEx(entry, f"org{i * 2 + 1:04d}", 0, winreg.REG_SZ,
                                  subtype)
                winreg.SetValueEx(entry, f"mod{i * 2:04d}", 0, winreg.REG_SZ,
                                  MEDIATYPE_VIDEO)
                winreg.SetValueEx(entry, f"mod{i * 2 + 1:04d}", 0, winreg.REG_SZ,
                                  subtype)


def unregister_filter_elevated(timeout: int = 120) -> tuple[bool, str]:
    """Unregister the DirectShow filter, asking Windows for elevation."""
    if not is_registered():
        return True, "not registered"
    if not FILTER_AX.is_file():
        return False, f"{FILTER_AX.name} not found"

    import ctypes
    import time

    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "regsvr32.exe", f'/s /u "{FILTER_AX}"', None, 0
    )
    if result <= 32:
        return False, f"elevation declined or failed (code {result})"

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_registered():
            return True, "unregistered"
        time.sleep(0.5)
    return False, "did not complete in time"


def backup_filters(destination: Path) -> Path | None:
    """Export MPC-HC's external filter list so the change can be undone."""
    try:
        key = _filters_key()
    except RuntimeError:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["reg", "export", f"HKCU\\{key}", str(destination), "/y"],
        capture_output=True, text=True,
    )
    return destination if proc.returncode == 0 else None


def remove_mpc_external_filter() -> bool:
    """Drop our entry from MPC-HC's external filter list, leaving others alone."""
    try:
        filters_key = _filters_key()
    except RuntimeError:
        return False

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, filters_key, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as parent:
            index = 0
            while True:
                try:
                    slot = winreg.EnumKey(parent, index)
                except OSError:
                    return False
                try:
                    with winreg.OpenKey(parent, slot) as entry:
                        found = winreg.QueryValueEx(entry, "Name")[0] == FILTER_NAME
                except OSError:
                    found = False
                if found:
                    winreg.DeleteKey(parent, slot)
                    return True
                index += 1
    except OSError:
        return False


def write_player_script(profile: Profile, source_fps: float = 23.976) -> Path:
    """Write the script the filter will run for every video.

    One script serves every file: it reads the frame rate at play time and
    works out the multiplier itself, so nothing is per-video.
    """
    SCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    avs.write_script(profile, SCRIPT_PATH)
    return SCRIPT_PATH


# --- status -----------------------------------------------------------------

def status() -> list[tuple[str, bool, str]]:
    mpc = find_mpc_hc()
    clsid = real_filter_clsid()
    script = get_script_path()
    return [
        ("MPC-HC", mpc is not None, str(mpc) if mpc else "not found"),
        ("DirectShow filter", clsid is not None,
         clsid or "not registered (needs admin)"),
        ("Script", bool(script) and Path(script).is_file(),
         script or "not set"),
        ("VapourSynth", detect.vapoursynth_dir() is not None,
         str(detect.vs_package_dir())),
    ]
