"""Interpolation profiles.

Mirrors the knobs SVP Pro exposes in its profile editor, mapped onto
whichever motion backend is active. One profile drives all three backends;
each backend translates the generic knobs into its own parameter space.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field, asdict
from fractions import Fraction
from pathlib import Path
from typing import Literal

# --- vocabulary -------------------------------------------------------------

Backend = Literal["rife", "mvtools", "svpflow"]

# Motion-vector grid size. Smaller = follows fine detail, costs more, and
# gets noisier on flat areas. SVP calls this "motion vectors grid".
GRID = {
    "small": 8,      # SVP "small (8px)"
    "medium": 16,    # SVP "medium (16px)"
    "large": 32,     # SVP "large (32px)"
}

# How aggressively to hide interpolation artifacts. Higher = falls back to
# blending/frame-repeat sooner, so fewer warps but less smoothness.
ARTIFACT_MASKING = {
    "disabled": 0,
    "weak": 50,
    "average": 100,
    "strong": 150,
}


@dataclass
class Profile:
    """A complete interpolation configuration."""

    name: str = "Film"
    description: str = ""

    # --- what to produce ---------------------------------------------------
    # "display" locks output to the monitor refresh rate (what SVP does by
    # default). "multiple" uses an integer/fraction multiplier of source fps.
    # "fixed" pins an explicit fps.
    rate_mode: Literal["display", "multiple", "fixed"] = "display"
    rate_multiplier: str = "2"        # parsed as Fraction, used when rate_mode="multiple"
    rate_fixed: float = 60.0          # used when rate_mode="fixed"
    max_output_fps: float = 180.0     # hard ceiling, protects against 4K meltdowns

    # --- engine ------------------------------------------------------------
    backend: Backend = "rife"
    rife_model: str = "4.10"          # vs-mlrt RIFE revision; auto-falls back if absent
    rife_scale: float = 1.0           # 0.5 halves the flow resolution -> ~2x faster
    rife_ensemble: bool = False       # slower, slightly cleaner edges
    rife_fp16: bool = True
    rife_low_latency: bool = True     # vsmlrt "video_player" frame ordering

    # TensorRT tuning. RIFE is a small network invoked once per generated frame,
    # so per-launch overhead dominates -- CUDA graphs replay the whole launch
    # sequence in one call, and fp16 output halves the tensor copied back.
    trt_cuda_graph: bool = True
    trt_fp16_output: bool = True
    trt_workspace_mb: int = 2048
    trt_optimization_level: int = 5   # slower engine build, faster inference

    # --- motion estimation (mvtools / svpflow) -----------------------------
    grid: Literal["small", "medium", "large"] = "medium"
    overlap: float = 0.5              # fraction of block size; 0.5 = half-overlap
    search_radius: int = 3            # px; wider catches fast pans, costs time
    refine: bool = True               # second-pass Recalculate at half grid size
    wide_search: bool = False         # exhaustive search, for heavy camera motion

    # --- quality / safety --------------------------------------------------
    artifact_masking: Literal["disabled", "weak", "average", "strong"] = "average"
    scene_change_threshold: int = 400  # SAD above this = cut, don't interpolate across it
    scene_detection: bool = True       # flag cuts so RIFE repeats rather than morphs
    blend_on_scene_change: bool = False
    motion_blur: float = 0.0          # 0 = off; adds shutter-like blur to generated frames

    # --- performance -------------------------------------------------------
    downscale_above_height: int = 0   # e.g. 1440 -> estimate motion on a smaller copy
    threads: int = 0                  # 0 = auto
    num_streams: int = 2              # RIFE/TensorRT concurrent streams

    # --- passthrough -------------------------------------------------------
    # Skip interpolation entirely when the source already exceeds this.
    bypass_above_fps: float = 59.0
    # Seconds to shift the sound. Negative = sound plays earlier.
    audio_delay: float = -0.8

    def resolve_target_fps(self, source_fps: float, display_fps: float) -> float:
        """Work out the fps this profile wants to produce."""
        if self.rate_mode == "display":
            target = display_fps
        elif self.rate_mode == "multiple":
            target = source_fps * float(Fraction(self.rate_multiplier))
        else:
            target = self.rate_fixed
        return min(target, self.max_output_fps)

    def filter_concurrency(self) -> int:
        """How many frames the host should keep in flight through the filter.

        VapourSynth parallelises across frame requests, not inside a single
        filter, so for a CPU backend this number *is* the thread count. Asking
        for two frames at a time on a twenty-core machine leaves eighteen cores
        idle and misses real time by an order of magnitude.

        RIFE moves the *inference* to the GPU but not the rest of the graph:
        every frame is still converted YUV -> RGBS and back on the CPU, at
        float32, before and after the network sees it. At 1440p/180 fps that
        conversion is the thing that starves the GPU, so the request count has
        to cover it as well as the TensorRT streams -- `num_streams` alone
        leaves both the CPU and the GPU idle waiting on each other. Three
        requests per stream keeps a conversion running for each stream that is
        busy inferring, with a floor of four and the core count as the ceiling.
        """
        if self.threads > 0:
            return self.threads
        cores = max(1, os.cpu_count() or 4)
        if self.backend == "rife":
            return max(4, min(cores, self.num_streams * 3))
        return cores

    def multiplier(self, source_fps: float, display_fps: float) -> Fraction:
        """Rational frame multiplier, which is what RIFE needs.

        The exact ratio is usually something like 7.5075 (23.976 -> 180). Feeding
        that in literally forces the interpolator to evaluate a huge spread of
        distinct timesteps for a difference nobody can see, so snap to the
        simplest fraction within tolerance -- 15/2 here. The player's
        display-resample stage absorbs the leftover 0.1%.
        """
        target = self.resolve_target_fps(source_fps, display_fps)
        if source_fps <= 0:
            return Fraction(2)
        exact = target / source_fps
        if exact <= 1:
            return Fraction(1)
        # Bias downward: overshooting the panel makes the player drop frames,
        # which reintroduces exactly the judder we are here to remove.
        import math
        for denom in range(1, 17):
            candidate = Fraction(math.floor(exact * denom), denom)
            if candidate >= 1 and (exact - float(candidate)) / exact <= 0.02:
                return candidate
        return Fraction(exact).limit_denominator(16)

    # --- serialisation -----------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def load(cls, path: Path) -> "Profile":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")


# --- presets ----------------------------------------------------------------
# Tuned to match the intent of SVP's stock profiles.

PRESETS: dict[str, Profile] = {
    "film": Profile(
        name="Film",
        description="24p movies. Block motion, which measures fastest on most machines.",
        backend="mvtools", grid="medium", artifact_masking="average",
    ),
    "anime": Profile(
        name="Anime",
        description="Flat cel shading, hard line art, held frames and frequent cuts.",
        backend="mvtools", grid="medium",
        artifact_masking="strong", scene_change_threshold=250,
        search_radius=4,
    ),
    "sports": Profile(
        name="Sports / Fast motion",
        description="Fast pans and erratic subject motion. Widest search.",
        backend="mvtools", grid="small", search_radius=5, wide_search=True,
        artifact_masking="weak", refine=True,
    ),
    "fast": Profile(
        name="Fast (highest sustainable rate)",
        description="Coarse grid. The cheapest setting that still looks smooth.",
        backend="mvtools", grid="large", artifact_masking="average",
    ),
    "neural": Profile(
        name="Neural (RIFE)",
        description=(
            "Best quality around occlusions and non-rigid motion, but far more "
            "expensive -- measure with `twixt tune` before relying on it."
        ),
        backend="rife", grid="medium", artifact_masking="weak",
    ),
    "4k": Profile(
        name="4K / heavy",
        description="Estimates motion on a smaller copy and caps output for 2160p.",
        backend="mvtools", grid="large", max_output_fps=120.0,
        downscale_above_height=1440,
    ),
    "max-quality": Profile(
        name="Max quality (may drop frames)",
        description="Finest grid and full refinement. Expect to lower the cap.",
        backend="mvtools", grid="small", refine=True, wide_search=True,
        artifact_masking="weak",
    ),
}


def get(name: str) -> Profile:
    key = name.lower().replace(" ", "-")
    if key in PRESETS:
        return dataclasses.replace(PRESETS[key])
    path = Path(name)
    if path.is_file():
        return Profile.load(path)
    raise KeyError(
        f"Unknown profile {name!r}. Built in: {', '.join(sorted(PRESETS))}, "
        "or pass a path to a .json profile."
    )
