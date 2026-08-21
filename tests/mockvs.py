"""A stand-in for VapourSynth so generated scripts can be exercised on any box.

It records every filter call, which lets the tests assert on the actual filter
graph the engine builds -- block sizes, multipliers, scene thresholds -- without
needing a GPU or a real VapourSynth install.
"""

from __future__ import annotations

import sys
import types

CALLS: list[tuple[str, dict]] = []


class Format:
    def __init__(self, fid, bits=8, family="YUV"):
        self.id = fid
        self.bits_per_sample = bits
        self.color_family = family
        self.name = f"{family}{bits}"


class Clip:
    def __init__(self, width=1920, height=1080, fps=23.976, fmt=None):
        from fractions import Fraction
        self.width = width
        self.height = height
        # VapourSynth exposes this as a Fraction, and mpv's video_in reports
        # 0/0 -- both shapes have to be representable here.
        self.fps = (fps if isinstance(fps, Fraction)
                    else Fraction(fps).limit_denominator(1000000))
        self.format = fmt or Format("YUV420P8")
        self.output = False

    def set_output(self, index=0):
        self.output = True
        CALLS.append(("set_output", {"clip": self}))

    # svp1.Super/Analyse return dict-like objects
    def __getitem__(self, key):
        return self


class _Namespace:
    def __init__(self, name):
        self._name = name

    def __getattr__(self, func):
        def call(*args, **kwargs):
            CALLS.append((f"{self._name}.{func}", dict(kwargs)))
            base = next((a for a in args if isinstance(a, Clip)), None) or Clip()
            out = Clip(base.width, base.height, base.fps, base.format)
            if "format" in kwargs:
                out.format = Format(kwargs["format"])
            if "width" in kwargs:
                out.width = kwargs["width"]
            if "height" in kwargs:
                out.height = kwargs["height"]
            return out
        return call


class _Core:
    def __getattr__(self, ns):
        return _Namespace(ns)


def install(with_vsmlrt: bool = True):
    """Register the fake modules in sys.modules and return the fake vs module."""
    CALLS.clear()

    vs = types.ModuleType("vapoursynth")
    vs.core = _Core()
    vs.RGBS = "RGBS"
    vs.YUV420P8 = "YUV420P8"
    vs.YUV = "YUV"
    vs.GRAY = "GRAY"
    sys.modules["vapoursynth"] = vs

    if with_vsmlrt:
        mlrt = types.ModuleType("vsmlrt")

        class _Backend:
            @staticmethod
            def TRT(**kw):
                CALLS.append(("Backend.TRT", dict(kw)))
                return "trt-backend"

        def RIFE(clip, **kw):
            CALLS.append(("vsmlrt.RIFE", dict(kw)))
            return clip

        mlrt.Backend = _Backend
        mlrt.RIFE = RIFE
        sys.modules["vsmlrt"] = mlrt
    else:
        sys.modules.pop("vsmlrt", None)
        # Make `import vsmlrt` raise ImportError so the ncnn path is taken.
        class _Blocker:
            def find_module(self, name, path=None):
                return self if name == "vsmlrt" else None

            def load_module(self, name):
                raise ImportError("vsmlrt blocked for test")

        sys.meta_path.insert(0, _Blocker())

    return vs


def uninstall():
    sys.meta_path[:] = [m for m in sys.meta_path if type(m).__name__ != "_Blocker"]
    sys.modules.pop("vapoursynth", None)
    sys.modules.pop("vsmlrt", None)


def run(script: str, source_fps: float = 23.976, display_fps: float = 180.0,
        width: int = 1920, height: int = 1080, with_vsmlrt: bool = True,
        host: str = "mpv"):
    """Execute a generated script the way one of its hosts would.

    host="mpv"   -> supplies video_in with no frame rate
    host="dshow" -> supplies VpsFilterSource, as the DirectShow filter does
    """
    install(with_vsmlrt=with_vsmlrt)
    try:
        from fractions import Fraction
        if host == "dshow":
            ns = {
                "VpsFilterSource": Clip(width, height, source_fps),
                "__name__": "__vapoursynth__",
            }
        else:
            ns = {
                # mpv supplies frames with no frame-rate metadata at all.
                "video_in": Clip(width, height, Fraction(0, 1)),
                "container_fps": source_fps,
                "display_fps": display_fps,
                "__name__": "__vapoursynth__",
            }
        exec(compile(script, "<generated.vpy>", "exec"), ns)
        return list(CALLS), ns
    finally:
        uninstall()


def find(calls, name):
    return [kw for fn, kw in calls if fn == name]
