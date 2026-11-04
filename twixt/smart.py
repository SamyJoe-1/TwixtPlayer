"""Auto mode: look at the file and the monitor, decide everything, explain why.

No waiting and no measuring here -- this is the instant pick. It leans on what
has actually been measured on this machine (twixt-checked.json from a live
check, else the 1440p numbers recorded in twixt-1440p.json) and says so in the
reasons it returns, so the choice is never a silent guess.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import detect, profiles

# --- ready profiles -----------------------------------------------------------

CONTENT = ("movies", "anime", "sports", "gaming")

LABELS = {
    "movies": "Movies / TV",
    "anime": "Anime",
    "sports": "Sports / action",
    "gaming": "Gaming / YouTube",
}


def content_profile(kind: str, backends: dict[str, bool]) -> profiles.Profile:
    """Settings for each kind of video, on top of the measured 1440p base."""
    base = _measured_base()
    p = dataclasses.replace(base)
    p.name = LABELS[kind]
    if kind == "movies":
        # Balanced: MVTools' default masking and cut thresholds.
        p.artifact_masking = "average"
        p.scene_change_threshold = 400
    elif kind == "anime":
        # Same intent as the stock anime preset: hard line art wobbles under
        # weak masking, and cuts are frequent, so mask harder and flag cuts
        # sooner (lower thSCD1 = more blocks counted as changed).
        p.artifact_masking = "strong"
        p.scene_change_threshold = 250
        p.search_radius = 4
    elif kind == "sports":
        # Fast, unpredictable motion is where block matching ghosts most, so
        # this is the case for the neural engine when it is installed.
        if backends.get("rife"):
            p.backend = "rife"
            p.artifact_masking = "weak"
        else:
            p.search_radius = 5
            p.wide_search = True
            p.artifact_masking = "weak"
    elif kind == "gaming":
        # 30/60 fps captures. The default bypass skips anything above 59 fps;
        # here 60 fps sources are the point, so let them through.
        p.bypass_above_fps = 61.0
        p.artifact_masking = "average"
        p.scene_change_threshold = 400
    return p


def _measured_base() -> profiles.Profile:
    for name in ("twixt-checked.json", "twixt-1440p.json"):
        path = detect.ROOT / name
        if path.is_file():
            try:
                return profiles.Profile.load(path)
            except Exception:
                pass
    return profiles.get("fast")


# --- content detection (file name) --------------------------------------------

_ANIME = re.compile(
    r"(\banime\b|\[(subsplease|erai-raws|horriblesubs|judas|ember|asw|anime time|"
    r"yameii|dkb|commie|gjm|kametsu|beatrice-raws|lostyears|toonshub|varyg|"
    r"sallysubs|cr|funimation)\]|^\s*\[[^\]]+\].*\s-\s\d{1,4}\b)",
    re.IGNORECASE)
_SPORTS = re.compile(
    r"\b(nba|nfl|nhl|mlb|ufc|wwe|f1|formula[ ._-]?1|motogp|premier[ ._-]league|"
    r"champions[ ._-]league|la[ ._-]liga|serie[ ._-]a|bundesliga|world[ ._-]cup|"
    r"highlights|grand[ ._-]prix|boxing)\b", re.IGNORECASE)
_GAMING = re.compile(
    r"\b(gameplay|walkthrough|playthrough|speedrun|twitch|obs|nvidia[ ._-]share|"
    r"shadowplay|clip|youtube|vlog)\b", re.IGNORECASE)


def detect_content(info: detect.SourceInfo) -> tuple[str, str]:
    """(kind, reason). File name first, then the frame rate."""
    name = info.path.name
    m = _ANIME.search(name)
    if m:
        return "anime", f'"{m.group(0).strip()}" in the file name'
    m = _SPORTS.search(name)
    if m:
        return "sports", f'"{m.group(0).strip()}" in the file name'
    m = _GAMING.search(name)
    if m:
        return "gaming", f'"{m.group(0).strip()}" in the file name'
    if info.fps >= 29.0:
        return "gaming", f"{info.fps:.3g} fps source (film is 24)"
    return "movies", "24 fps film"


# --- the plan -----------------------------------------------------------------

@dataclass
class Plan:
    profile: profiles.Profile
    fps: float
    content: str
    reasons: list[str] = field(default_factory=list)
    warning: str = ""

    @property
    def engine(self) -> str:
        return "RIFE · GPU" if self.profile.backend == "rife" else "MVTools · CPU"


def smooth_rates(display_hz: float, source_fps: float = 23.976) -> list[float]:
    """Rates the monitor shows evenly (display / k), at least 60 and 2x the source."""
    floor = max(60.0, source_fps * 2) - 0.5
    rates = [display_hz / k for k in range(1, 9) if display_hz / k >= floor]
    return [round(r, 2) for r in rates][:3] or [display_hz]


def safe_ceiling(info: detect.SourceInfo) -> tuple[float, str]:
    """Highest output rate known to hold on this PC, and where that comes from."""
    checked = detect.ROOT / "twixt-checked.json"
    if checked.is_file():
        try:
            p = profiles.Profile.load(checked)
            m = re.search(r"(\d+)x(\d+) @ ([\d.]+)fps", p.name)
            if m and info.height and int(m.group(2)) >= info.height:
                return float(m.group(3)), "your live test"
        except Exception:
            pass
    # Recorded in twixt-1440p.json on this machine: 1440p mvtools held 90 fps
    # at 1.44x and 180 measured 0.83x. Smaller frames cost less, so 90 is a
    # floor for anything up to 1440p, not a ceiling -- but nothing above it
    # has been measured.
    return 90.0, "measured 1440p result"


def plan(info: detect.SourceInfo, display_hz: float, content: str = "auto",
         fps: float | str = "auto", backends: dict[str, bool] | None = None) -> Plan:
    """Everything the Play button needs, plus a plain-language why."""
    backends = backends if backends is not None else detect.available_backends()
    reasons: list[str] = []

    if content == "auto":
        content, why = detect_content(info)
        reasons.append(f"{LABELS[content]}: {why}")
    prof = content_profile(content, backends)

    warning = ""
    if fps == "auto":
        rates = smooth_rates(display_hz, info.fps)
        ceiling, source = safe_ceiling(info)
        fitting = [r for r in rates if r <= ceiling + 0.5]
        if fitting:
            target = max(fitting)
            reasons.append(f"{target:g} fps: highest even rate for {display_hz:g} Hz "
                           f"within the {source} ({ceiling:g})")
        else:
            target = min(rates)
            warning = (f"{target:g} fps is above what has been measured here "
                       f"({ceiling:g}); it may drop frames")
            reasons.append(f"{target:g} fps: the only even rate above a "
                           f"{info.fps:.3g} fps source on {display_hz:g} Hz")
        prof.snap_to_display = True
    else:
        target = float(fps)
        prof.snap_to_display = False
        ratio = display_hz / target if target else 0
        if display_hz and abs(ratio - round(ratio)) > 0.02:
            warning = (f"{target:g} fps on a {display_hz:g} Hz monitor: frames are held "
                       f"unevenly ({ratio:.2f} refreshes each), so motion may judder")

    prof.rate_mode, prof.rate_fixed, prof.max_output_fps = "fixed", target, target
    if info.fps >= target - 0.5:
        warning = (f"the video is already {info.fps:.3g} fps, at or above {target:g}: "
                   "it will play without frame generation")
    return Plan(profile=prof, fps=target, content=content, reasons=reasons, warning=warning)
