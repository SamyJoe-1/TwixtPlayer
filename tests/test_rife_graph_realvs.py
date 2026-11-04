"""Runs the generated RIFE graph inside REAL VapourSynth.

The TensorRT model is replaced by an exact linear blend (a*(1-t) + b*t), so
every output frame's value tells us which two source frames it came from and
at what time point. Each source frame n carries the value n/10; output frame
k must therefore read k/multi (to fp16 precision), cuts must hold the frame,
and the frame durations mpv uses for timestamps must be exact.

Skips itself when VapourSynth is not importable:  pip install vapoursynth
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import vapoursynth as vs
except ImportError:          # pragma: no cover
    vs = None

from twixt import engine, profiles   # noqa: E402


def _fake_vsmlrt():
    core = vs.core
    mod = types.ModuleType("vsmlrt")

    class Backend:
        class TRT:
            def __init__(self, **kw):
                pass

    def RIFEMerge(a, b, m, **kw):
        assert a.num_frames == b.num_frames == m.num_frames
        mm = core.std.ShufflePlanes([m, m, m], [0, 0, 0], vs.RGB)
        return core.std.Expr([a, b, mm], "x 1 z - * y z * +")

    mod.Backend, mod.RIFEMerge = Backend, RIFEMerge
    return mod


def _run(src_fps, display, target, n_src=40, cut_at=None):
    core = vs.core
    sys.modules["vsmlrt"] = _fake_vsmlrt()
    try:
        p = profiles.get("neural")
        p.rate_mode, p.rate_fixed, p.max_output_fps = "fixed", target, 1000.0
        p.scene_detection = False
        frames = [core.std.BlankClip(width=64, height=32, format=vs.YUV444PS,
                                     color=[n / 10, 0, 0], length=1) for n in range(n_src)]
        if cut_at is not None:
            frames[cut_at] = core.std.SetFrameProp(frames[cut_at], prop="_SceneChangeNext", intval=1)
        # mpv's video_in carries no frame rate (0/0); the script stamps one.
        src = core.std.Splice(frames)
        clip = core.std.FrameEval(core.std.BlankClip(src, fpsnum=0, fpsden=1, keep=True),
                                  lambda n: src[n])
        ns = {"video_in": clip, "container_fps": src_fps, "display_fps": display}
        exec(compile(engine.generate_script(p, src_fps, display), "<g>", "exec"), ns)
        out = vs.get_output(0)
        out = getattr(out, "clip", out)
        return out, ns["MULTI"]
    finally:
        vs.clear_outputs()
        sys.modules.pop("vsmlrt", None)


def test_every_output_frame_sits_at_the_right_time():
    if vs is None:
        print("  (skipped: vapoursynth not installed)")
        return
    for src, disp, want in [(23.976, 180, 90), (23.976, 180, 180), (24.0, 120, 120),
                            (23.976, 144, 144), (25.0, 180, 90)]:
        out, multi = _run(src, disp, want)
        for k in range(int(38 * multi)):
            got = out.get_frame(k)[0][0, 0] * 10
            assert abs(got - k / float(multi)) < 0.02, (src, disp, k, got)


def test_scene_cut_holds_the_frame_and_durations_are_exact():
    if vs is None:
        print("  (skipped: vapoursynth not installed)")
        return
    out, multi = _run(23.976, 180, 90, n_src=30, cut_at=10)
    for k in range(int(9 * multi), int(12 * multi)):
        t = k / float(multi)
        if 10 <= t < 11:
            assert round(out.get_frame(k)[0][0, 0] * 10, 2) == 10.0
    f = out.get_frame(3)
    assert (f.props["_DurationNum"], f.props["_DurationDen"]) == (1001, 90000)


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
