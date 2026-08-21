"""Frame-rate arithmetic: the part most likely to produce judder if wrong."""

from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from twixt import profiles  # noqa: E402

# (source fps, panel Hz) pairs covering the formats people actually own.
REAL_WORLD = [
    (23.976, 180.0), (24.0, 180.0), (25.0, 180.0), (29.97, 180.0),
    (30.0, 180.0), (48.0, 180.0), (50.0, 180.0),
    (23.976, 60.0), (23.976, 144.0), (23.976, 165.0), (25.0, 144.0),
    (29.97, 120.0), (23.976, 240.0),
]


def test_output_never_exceeds_the_panel():
    """Overshooting forces the player to drop frames, reintroducing judder."""
    for src, panel in REAL_WORLD:
        p = profiles.get("film")
        p.max_output_fps = panel
        out = src * float(p.multiplier(src, panel))
        assert out <= panel + 0.01, f"{src} -> {panel}Hz produced {out}"


def test_multiplier_stays_simple():
    """A huge denominator means many distinct timesteps for no visible gain."""
    for src, panel in REAL_WORLD:
        p = profiles.get("film")
        p.max_output_fps = panel
        assert p.multiplier(src, panel).denominator <= 16


def test_multiplier_is_close_to_the_ideal_ratio():
    for src, panel in REAL_WORLD:
        p = profiles.get("film")
        p.max_output_fps = panel
        exact = min(panel, p.max_output_fps) / src
        got = float(p.multiplier(src, panel))
        assert (exact - got) / exact <= 0.02, f"{src}->{panel}: {got} vs {exact}"


def test_24p_to_180hz_is_seven_and_a_half():
    assert profiles.get("film").multiplier(24.0, 180.0) == Fraction(15, 2)


def test_already_fast_source_is_not_multiplied():
    p = profiles.get("film")
    assert p.multiplier(60.0, 60.0) == Fraction(1)
    assert p.multiplier(120.0, 60.0) == Fraction(1)


def test_cap_is_respected_over_the_display_rate():
    p = profiles.get("film")
    p.max_output_fps = 120.0
    assert p.resolve_target_fps(24.0, 180.0) == 120.0
    assert p.multiplier(24.0, 180.0) == Fraction(5)


def test_fixed_and_multiple_rate_modes():
    p = profiles.get("film")
    p.rate_mode, p.rate_fixed = "fixed", 60.0
    assert p.resolve_target_fps(23.976, 180.0) == 60.0

    p.rate_mode, p.rate_multiplier = "multiple", "5/2"
    assert p.resolve_target_fps(24.0, 180.0) == 60.0

    # A multiplier that would exceed the ceiling is clamped, not obeyed.
    p.rate_multiplier = "20"
    assert p.resolve_target_fps(24.0, 180.0) == p.max_output_fps


def test_zero_and_broken_source_fps_do_not_crash():
    p = profiles.get("film")
    assert p.multiplier(0.0, 180.0) == Fraction(2)
    assert p.resolve_target_fps(0.0, 180.0) > 0


def test_roundtrip_through_json(tmp_path=None):
    import tempfile
    p = profiles.get("anime")
    p.grid, p.max_output_fps, p.rife_ensemble = "large", 144.0, True
    path = Path(tempfile.gettempdir()) / "twixt-profile-test.json"
    p.save(path)
    loaded = profiles.Profile.load(path)
    path.unlink(missing_ok=True)
    assert (loaded.grid, loaded.max_output_fps, loaded.rife_ensemble) == \
           ("large", 144.0, True)
    assert loaded.backend == p.backend


def test_unknown_profile_name_is_reported_clearly():
    try:
        profiles.get("does-not-exist")
    except KeyError as exc:
        assert "film" in exc.args[0], "the error should list valid options"
    else:
        raise AssertionError("expected a KeyError")


def test_presets_are_independent_copies():
    a = profiles.get("film")
    a.grid = "large"
    assert profiles.get("film").grid == "medium", "presets must not be mutated"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except Exception as exc:
                failed += 1
                print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print("\nall green" if not failed else f"\n{failed} failing")
    sys.exit(1 if failed else 0)
