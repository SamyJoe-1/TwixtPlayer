# TwixtPlayer: project summary

_Last full analysis: 2026-11-04 (branch `cloned/zealous-goldberg-ejnvs7`)_

Read this first instead of re-scanning the repo. Update it when the structure changes.

## What it is
Real-time video frame interpolation for playback on Windows. It does the same job as SVP Pro, built from open parts:
**mpv** (player) → **VapourSynth** filter (`--vf=vapoursynth=...`) → motion engine
(**mvtools** on the CPU, **RIFE** through vs-mlrt/TensorRT on the GPU, or **svpflow** if SVP is installed).
Python generates a per-file `.vpy` script and launches mpv. Target machine: RTX 4070 SUPER, i5-14600K (20 threads), 2560x1440 at 180 Hz.

## Files
| File | Role |
|---|---|
| `twixt/profiles.py` | `Profile` dataclass (all knobs), `PRESETS`, `GRID` (8/16/32 px), `ARTIFACT_MASKING` (SVP scale), `MVTOOLS_ML` (FlowFPS ml, inverted scale), `SCENE_CHANGE_RATIO`=130, `snap_to_cadence()`, `resolve_target_fps()`, `multiplier()` (fraction, denominator ≤ 16, biased down), `filter_concurrency()` |
| `twixt/engine.py` | Builds the `.vpy` from `_HEADER` + `_BODY` templates, with the config embedded as the `CFG` literal. Hosts: mpv (`video_in`), dshow (`VpsFilterSource`), standalone (vspipe). Backends: `_interp_mvtools` (Super→Analyse→Recalculate→FlowFPS), `_interp_rife` → `_rife_native` (native phase graph plus `vsmlrt.RIFEMerge`, akarin.Select for cuts), `_interp_svpflow`. `_enhance` = optional RealESRGAN/DPIR. `build_config()` flattens the profile |
| `twixt/player.py` | mpv args (`--hwdec=auto-copy`, `--video-sync=display-resample`, `--vo=gpu-next`, `--profile=high-quality`, `--interpolation=no`, `--hr-seek-framedrop=no`, audio-delay), `_vf_arg` (buffered/concurrent frames), IPC `Session`, input.conf (TAB toggles the filter, BS shows stats), and `benchmark()` (vspipe slope timing) |
| `twixt/detect.py` | runtime paths under `runtime/`, refresh rate (Win32_VideoController), ffprobe `probe()`, `player_env()` |
| `twixt/cli.py` | commands: play / bench / tune (tests the display/k targets) / script / doctor / profiles / gui / install-mpchc. Flags: `--profile --backend --fps --multiplier --cap --grid --masking --no-snap` |
| `twixt/gui.py` | Tk manager window |
| `twixt/avs.py`, `mpchc.py`, `_mpchc_cli.py` | AviSynth/MPC-HC path (does not work: MPC-HC will not load the filter; see README) |
| `twixt/osd/twixt-fps.lua` | fps label that shows with the OSC |
| `bootstrap.ps1` | installs mpv, VapourSynth portable, vsrepo plugins `mv bs akarin`, vs-mlrt TensorRT and models, rife-ncnn |
| `*.json` (root) | user profiles: `twixt-1440p.json` (mvtools 90), `twixt-1440p-120hz.json` (needs the 120 Hz panel), `twixt-1440p-rife.json` (GPU 90), `twixt-enhance.json` |
| `play.cmd` / `play-rife.cmd` | drag-and-drop launchers |
| `tests/` | `mockvs.py` (fake VS that records calls), `test_engine/player/profiles.py`, `test_rife_graph_realvs.py` (real VS plus a linear-blend RIFEMerge stand-in) |

## Key facts / decisions (sourced)
- mpv vf_vapoursynth: the buffer size is buffered-frames × concurrent-frames; a larger buffer gives no speed-up (mpv manual).
- MVTools: thSCD1 = 400 and thSCD2 = 130 by default. On a detected cut FlowFPS repeats the frame (blend=false). `ml` higher = weaker mask. Up to 16-bit input is supported.
- vs-mlrt: `RIFE(video_player=True)` with a fractional multi uses Python FrameEval per frame, which caused the GIL bottleneck. `RIFEMerge` needs float16/32 RGB. Padding multiple is 32, or 64 for 4.26+ and 4.25-heavy, or 128 for 4.25-lite. `scale != 1` is not supported for models ≥ 4.7.
- Cadence: output must equal display/k or frames are held unevenly. On 180 Hz: 180 / 90 / 60.

## Change log
- 2026-11-04: cadence snapping, native RIFE graph with RGBH, MVTools scene thresholds back to defaults, buffered-frames fix, no output-rate reconversion, ml inversion fix, exact NTSC rates, tune tests display/k, akarin in bootstrap, RIFE profile and launcher.
