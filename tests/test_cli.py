"""tune and check decision logic, with the measurements faked."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from twixt import cli, detect, player, profiles   # noqa: E402

COST = {"large": 1.0, "medium": 1.8, "small": 3.5}   # relative filter cost


def _fake_env(monkey):
    tmp = Path(tempfile.mkdtemp())
    clip = tmp / "film.mkv"
    clip.write_bytes(b"x")
    monkey["display_refresh_hz"] = detect.display_refresh_hz
    monkey["probe"] = detect.probe
    monkey["available_backends"] = detect.available_backends
    monkey["prune"] = detect.prune_failed_engines
    detect.display_refresh_hz = lambda default=60.0: 180.0
    detect.probe = lambda p: detect.SourceInfo(Path(p), fps=23.976, width=2560, height=1440)
    detect.available_backends = lambda: {"mvtools": True, "rife": False, "svpflow": False}
    detect.prune_failed_engines = lambda: []
    return clip, tmp


def _restore(monkey):
    detect.display_refresh_hz = monkey["display_refresh_hz"]
    detect.probe = monkey["probe"]
    detect.available_backends = monkey["available_backends"]
    detect.prune_failed_engines = monkey["prune"]


def test_tune_picks_highest_even_rate_then_finest_setting_that_holds():
    monkey, seen = {}, []
    clip, tmp = _fake_env(monkey)
    orig = player.benchmark

    def bench(path, profile, frames=0):
        pel = 2 if profile.downscale_above_height == 0 else 1
        cost = COST[profile.grid] * (1.4 if pel == 2 else 1.0)
        achieved = 200.0 / cost            # large/p1 = 200 fps ... small/p2 = 41
        target = profile.resolve_target_fps(23.976, 180.0)
        seen.append((profile.grid, pel, target))
        return {"source_fps": 23.976, "target_fps": target, "achieved_fps": achieved,
                "realtime": achieved >= target, "headroom": achieved / target,
                "resolution": "2560x1440", "elapsed": 1, "frames": frames,
                "start_frame": 0, "index_seconds": 0, "overhead_seconds": 0}
    player.benchmark = bench
    out = tmp / "tuned.json"
    try:
        rc = cli.main(["tune", str(clip), "-o", str(out)])
    finally:
        player.benchmark = orig
        _restore(monkey)
    assert rc == 0
    # 180 fails even on the cheapest setting -> only one 180 measurement.
    assert [s for s in seen if s[2] == 180.0] == [("large", 1, 180.0)]
    # At 90 (needs 112.5 with the 1.25 margin): large/p1 200, large/p2 143,
    # medium/p1 111 -> medium/p1 fails, so large/p2 is the best that holds.
    p = profiles.Profile.load(out)
    assert p.grid == "large" and p.downscale_above_height == 0, (p.grid, p.downscale_above_height)
    assert p.resolve_target_fps(23.976, 180.0) == 90.0


class _FakeSession:
    def __init__(self, ratio, drops_after, clock):
        self.ratio, self.drops_after, self.calls, self.clock = ratio, drops_after, 0, clock
        self.target_fps = 90.0
        self.script = Path(tempfile.mkdtemp()) / "x.vpy"

        class P:
            def poll(self):
                return None

            def wait(self, timeout=None):
                return 0
        self.process = P()
        self.t = 0.0

    def get(self, prop):
        self.calls += 1
        if prop == "time-pos":
            return self.clock[0]
        if prop == "vsync-ratio":
            return self.ratio
        if prop == "frame-drop-count":
            return self.drops_after if self.clock[0] > 8 else 0
        if prop == "display-sync-active":
            return True
        if prop == "estimated-vf-fps":
            return 89.9
        return 0

    def command(self, *a):
        return {}


def _live(ratio, drops):
    orig_launch, orig_sleep, orig_mono = player.launch, player.time.sleep, player.time.monotonic
    clock = [0.0]
    player.time.sleep = lambda s: clock.__setitem__(0, clock[0] + s)
    player.time.monotonic = lambda: clock[0]
    sess = _FakeSession(ratio, drops, clock)
    player.launch = lambda *a, **k: sess
    try:
        return player.live_check("f.mkv", profiles.get("film"), seconds=10, warmup=1)
    finally:
        player.launch, player.time.sleep, player.time.monotonic = orig_launch, orig_sleep, orig_mono


def test_live_check_smooth_only_with_even_cadence_and_no_drops():
    r = _live(2.0, 0)
    assert r["smooth"] and r["even_cadence"] and r["drops"] == 0
    r = _live(1.5, 0)                      # 120 on 180 Hz
    assert not r["smooth"] and not r["even_cadence"]
    r = _live(2.0, 7)
    assert not r["smooth"] and r["drops"] == 7


if __name__ == "__main__":
    failed = 0
    for fname, fn in sorted(globals().items()):
        if fname.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {fname}")
            except Exception as exc:
                import traceback; traceback.print_exc()
                failed += 1
                print(f"  FAIL  {fname}: {type(exc).__name__}: {exc}")
    print("\nall green" if not failed else f"\n{failed} failing")
    sys.exit(1 if failed else 0)
