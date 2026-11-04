"""Auto mode decisions: content detection, fps choice, warnings."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from twixt import detect, smart   # noqa: E402

GPU = {"mvtools": True, "rife": True, "svpflow": False}
CPU = {"mvtools": True, "rife": False, "svpflow": False}


def _info(name, fps=23.976, w=2560, h=1440):
    return detect.SourceInfo(Path(name), fps=fps, width=w, height=h)


def test_detects_content_from_the_name():
    cases = {
        "[SubsPlease] Frieren - 05 (1080p) [A1B2C3].mkv": "anime",
        "[Erai-raws] One Piece - 1100 [1080p].mkv": "anime",
        "My Anime Collection - Akira.mkv": "anime",
        "NBA 2024 Finals Game 7.mkv": "sports",
        "F1 2024 Monaco Grand Prix Highlights.mp4": "sports",
        "Elden Ring gameplay part 3.mp4": "gaming",
        "Dune.Part.Two.2024.2160p.mkv": "movies",
        "Match Point (2005).mkv": "movies",
        "Alien vs Predator (2004).mkv": "movies",
    }
    for name, want in cases.items():
        assert smart.detect_content(_info(name))[0] == want, name


def test_frame_rate_decides_when_the_name_does_not():
    assert smart.detect_content(_info("recording.mp4", fps=60.0))[0] == "gaming"
    assert smart.detect_content(_info("recording.mp4", fps=29.97))[0] == "gaming"


def test_auto_plays_the_highest_measured_even_rate():
    plan = smart.plan(_info("Dune.mkv"), 180.0, backends=CPU)
    assert plan.fps == 90.0 and plan.content == "movies" and not plan.warning
    assert plan.profile.resolve_target_fps(23.976, 180.0) == 90.0
    assert plan.reasons, "always says why"


def test_anime_gets_stronger_masking_and_cut_detection():
    plan = smart.plan(_info("[SubsPlease] Frieren - 05.mkv"), 180.0, backends=CPU)
    assert plan.profile.artifact_masking == "strong"
    assert plan.profile.scene_change_threshold < 400


def test_sports_uses_the_gpu_when_installed():
    assert smart.plan(_info("NBA Finals.mkv"), 180.0, backends=GPU).profile.backend == "rife"
    cpu = smart.plan(_info("NBA Finals.mkv"), 180.0, backends=CPU).profile
    assert cpu.backend == "mvtools" and cpu.wide_search


def test_60fps_source_is_interpolated_not_bypassed():
    plan = smart.plan(_info("clip.mp4", fps=60.0, w=1920, h=1080), 180.0, backends=CPU)
    assert plan.fps == 180.0
    assert plan.profile.bypass_above_fps > 60
    assert "measured" in plan.warning, "180 was never measured here -- must say so"


def test_custom_fps_plays_exactly_and_warns_when_uneven():
    plan = smart.plan(_info("Dune.mkv"), 180.0, fps=120, backends=CPU)
    assert plan.fps == 120.0 and not plan.profile.snap_to_display
    assert plan.profile.resolve_target_fps(23.976, 180.0) == 120.0
    assert "judder" in plan.warning
    even = smart.plan(_info("Dune.mkv"), 180.0, fps=60, backends=CPU)
    assert not even.warning


def test_custom_fps_below_the_source_says_it_will_not_interpolate():
    plan = smart.plan(_info("clip.mp4", fps=60.0), 180.0, content="gaming", fps=60, backends=CPU)
    assert "without frame generation" in plan.warning


if __name__ == "__main__":
    failed = 0
    for fname, fn in sorted(globals().items()):
        if fname.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {fname}")
            except Exception as exc:
                failed += 1
                print(f"  FAIL  {fname}: {type(exc).__name__}: {exc}")
    print("\nall green" if not failed else f"\n{failed} failing")
    sys.exit(1 if failed else 0)
