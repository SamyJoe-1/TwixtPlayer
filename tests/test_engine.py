"""Executes generated scripts against the VapourSynth mock and asserts on the
resulting filter graph."""

from __future__ import annotations

import ast
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from twixt import engine, profiles           # noqa: E402
from tests import mockvs                    # noqa: E402


def _script(name, **over):
    p = profiles.get(name)
    for k, v in over.items():
        setattr(p, k, v)
    return engine.generate_script(p, 23.976, 180.0)


def test_all_presets_generate_valid_python():
    for name in profiles.PRESETS:
        ast.parse(_script(name))


def test_rife_receives_snapped_multiplier():
    calls, _ = mockvs.run(_script("neural"), 23.976, 180.0)
    rife = mockvs.find(calls, "vsmlrt.RIFE")
    assert len(rife) == 1, "RIFE should be invoked exactly once"
    assert rife[0]["multi"] == Fraction(15, 2)
    assert rife[0]["video_player"] is True, "playback path must use low-latency ordering"


def test_rife_roundtrips_through_rgbs_and_back():
    calls, _ = mockvs.run(_script("neural"))
    fmts = [kw.get("format") for fn, kw in calls if fn == "resize.Bicubic"]
    assert fmts[0] == "RGBS", "RIFE needs float RGB input"
    assert fmts[-1] == "YUV420P8", "must convert back for the player"


def test_falls_back_to_ncnn_when_vsmlrt_missing():
    calls, _ = mockvs.run(_script("neural"), with_vsmlrt=False)
    assert not mockvs.find(calls, "vsmlrt.RIFE")
    ncnn = mockvs.find(calls, "rife.RIFE")
    assert len(ncnn) == 1
    assert (ncnn[0]["factor_num"], ncnn[0]["factor_den"]) == (15, 2)
    assert ncnn[0]["sc"] is True, "scene-cut detection must stay on"


def test_high_framerate_source_is_passed_through_untouched():
    calls, _ = mockvs.run(_script("neural"), source_fps=120.0, display_fps=180.0)
    assert not mockvs.find(calls, "vsmlrt.RIFE")
    assert not mockvs.find(calls, "mv.FlowFPS")
    assert mockvs.find(calls, "set_output"), "clip must still reach the player"


def test_display_rate_equal_to_source_is_a_noop():
    calls, _ = mockvs.run(_script("neural"), source_fps=60.0, display_fps=60.0)
    assert not mockvs.find(calls, "vsmlrt.RIFE")


def test_mvtools_graph_shape():
    calls, _ = mockvs.run(_script("film"))
    assert len(mockvs.find(calls, "mv.Super")) == 2, "estimation + full-res supers"
    assert len(mockvs.find(calls, "mv.Analyse")) == 2, "backward + forward vectors"
    assert len(mockvs.find(calls, "mv.Recalculate")) == 2, "refine pass enabled"
    fps = mockvs.find(calls, "mv.FlowFPS")[0]
    assert fps["num"] / fps["den"] <= 180.0
    assert fps["thscd1"] == 400


def test_mvtools_overlap_is_even_and_below_block_size():
    for grid in ("small", "medium", "large"):
        calls, _ = mockvs.run(_script("film", grid=grid))
        blk = profiles.GRID[grid]
        for kw in mockvs.find(calls, "mv.Analyse"):
            assert kw["overlap"] % 2 == 0, f"{grid}: overlap must be even"
            assert 0 < kw["overlap"] < blk, f"{grid}: overlap must be under block size"
        for kw in mockvs.find(calls, "mv.Recalculate"):
            assert kw["overlap"] % 2 == 0
            assert 0 < kw["overlap"] < kw["blksize"]


def test_anime_profile_tightens_scene_detection():
    calls, _ = mockvs.run(_script("anime", backend="mvtools"))
    assert mockvs.find(calls, "mv.FlowFPS")[0]["thscd1"] == 250


def test_4k_profile_estimates_motion_on_a_smaller_copy():
    calls, _ = mockvs.run(_script("4k", backend="mvtools"), width=3840, height=2160)
    downscales = [kw for kw in mockvs.find(calls, "resize.Bicubic") if "height" in kw]
    assert downscales, "4K should trigger a downscaled estimation copy"
    assert downscales[0]["height"] <= 1440
    assert downscales[0]["width"] % 4 == 0, "mod-4 keeps chroma subsampling legal"


def test_4k_profile_caps_output_rate():
    p = profiles.get("4k")
    assert p.resolve_target_fps(23.976, 180.0) == 120.0


def test_svpflow_backend_builds_expected_chain():
    calls, _ = mockvs.run(_script("film", backend="svpflow"))
    assert mockvs.find(calls, "svp1.Super")
    assert mockvs.find(calls, "svp1.Analyse")
    assert mockvs.find(calls, "svp2.SmoothFps")


def test_motion_blur_only_applied_when_requested():
    assert not mockvs.find(mockvs.run(_script("film"))[0], "mv.FlowBlur")
    calls, _ = mockvs.run(_script("film", motion_blur=0.5))
    assert mockvs.find(calls, "mv.FlowBlur")


def test_output_is_always_set():
    for name in profiles.PRESETS:
        for src in (23.976, 60.0, 120.0):
            calls, _ = mockvs.run(_script(name), source_fps=src)
            assert mockvs.find(calls, "set_output"), f"{name} @ {src} produced no output"


def test_missing_frame_rate_from_mpv_is_stamped_on():
    """mpv hands over a clip with fps 0/0.

    MVTools rejects such a clip outright ("The input clip must have a frame
    rate"), and mpv responds by disabling the filter and playing normally --
    a silent loss of interpolation. AssumeFPS has to run first.
    """
    calls, _ = mockvs.run(_script("film"), source_fps=23.976)
    assumed = mockvs.find(calls, "std.AssumeFPS")
    assert assumed, "no AssumeFPS: the filter would be disabled by mpv"
    rate = assumed[0]["fpsnum"] / assumed[0]["fpsden"]
    assert abs(rate - 23.976) < 0.001

    # And it must happen before any filter that depends on the rate.
    names = [fn for fn, _ in calls]
    assert names.index("std.AssumeFPS") < names.index("mv.Super")


def test_frame_rate_is_not_restamped_when_already_present():
    from fractions import Fraction
    import tests.mockvs as m
    m.install()
    try:
        ns = {"video_in": m.Clip(1920, 1080, Fraction(24, 1)),
              "container_fps": 24.0, "display_fps": 180.0}
        exec(compile(_script("film"), "<g>", "exec"), ns)
        assert not m.find(list(m.CALLS), "std.AssumeFPS"),             "a clip that already has a rate should be left alone"
    finally:
        m.uninstall()


def test_directshow_host_uses_its_own_source_variable():
    """MPC-HC's VapourSynth Filter passes the clip as VpsFilterSource."""
    calls, ns = mockvs.run(_script("film"), source_fps=23.976, host="dshow")
    assert mockvs.find(calls, "mv.FlowFPS"), "interpolation should still run"
    assert mockvs.find(calls, "set_output")
    assert ns.get("_HOST") == "dshow"


def test_directshow_host_disconnects_instead_of_passing_through():
    """A 60 fps source should remove the filter from the graph, not copy frames."""
    calls, ns = mockvs.run(_script("film"), source_fps=60.0, host="dshow")
    assert ns.get("VpsFilterDisconnect") is True
    assert not mockvs.find(calls, "set_output"), "disconnecting means no output node"
    assert not mockvs.find(calls, "mv.FlowFPS")


def test_mpv_host_still_passes_through_rather_than_disconnecting():
    calls, ns = mockvs.run(_script("film"), source_fps=60.0, host="mpv")
    assert "VpsFilterDisconnect" not in ns
    assert mockvs.find(calls, "set_output")


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
