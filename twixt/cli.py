"""Command line front end."""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time as _time
from pathlib import Path

from . import detect, player, profiles
from .engine import generate_script

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


# Width of the in-place status line. Wide enough for the longest row it draws,
# so overwriting one never leaves a tail of the previous message behind.
_STATUS_WIDTH = 62


def _status(text: str) -> None:
    """Draw an in-place progress line -- no newline, overwritten by the result.

    Deliberately uncoloured: escape codes would throw off the padding that
    keeps the previous line fully covered.
    """
    if not sys.stdout.isatty():
        return          # redirected: \r would leave both versions on one line
    sys.stdout.write("\r  " + text.ljust(_STATUS_WIDTH))
    sys.stdout.flush()


def _endstatus(row: str) -> None:
    """Replace the current status line with a finished row."""
    if sys.stdout.isatty():
        sys.stdout.write("\r" + " " * (_STATUS_WIDTH + 4) + "\r")
    sys.stdout.write(row + "\n")
    sys.stdout.flush()


def _apply_overrides(profile: profiles.Profile, args) -> profiles.Profile:
    if args.backend:
        profile.backend = args.backend
    if args.fps:
        profile.rate_mode, profile.rate_fixed = "fixed", args.fps
    if args.multiplier:
        profile.rate_mode, profile.rate_multiplier = "multiple", args.multiplier
    if args.cap:
        profile.max_output_fps = args.cap
    if args.grid:
        profile.grid = args.grid
    if args.masking:
        profile.artifact_masking = args.masking
    return profile


def cmd_doctor(args) -> int:
    print(f"\n{BOLD}twixt environment{RESET}\n")
    rows = detect.diagnose()
    width = max(len(name) for name, _, _ in rows)
    for name, ok, detail in rows:
        mark = f"{GREEN}ok{RESET}" if ok else f"{RED}--{RESET}"
        print(f"  [{mark}] {name:<{width}}  {DIM}{detail}{RESET}")

    backends = detect.available_backends()
    usable = [b for b, present in backends.items() if present]
    print()
    if usable:
        print(f"  Usable backends: {GREEN}{', '.join(usable)}{RESET}")
    else:
        print(f"  {RED}No interpolation backend installed.{RESET} Run:  "
              f"{BOLD}powershell -ExecutionPolicy Bypass -File bootstrap.ps1{RESET}")
    print()
    return 0 if usable else 1


def cmd_profiles(args) -> int:
    print(f"\n{BOLD}built-in profiles{RESET}\n")
    for key, prof in profiles.PRESETS.items():
        print(f"  {BOLD}{key:<12}{RESET} {prof.name}")
        print(f"  {' ':<12} {DIM}{prof.description}{RESET}")
        print(f"  {' ':<12} {DIM}backend={prof.backend} grid={prof.grid} "
              f"masking={prof.artifact_masking} cap={prof.max_output_fps:g}fps{RESET}\n")
    return 0


def cmd_script(args) -> int:
    profile = _apply_overrides(profiles.get(args.profile), args)
    info = detect.probe(args.file) if args.file else detect.SourceInfo(Path("input"))
    text = generate_script(
        profile, info.fps, detect.display_refresh_hz(),
        standalone_source=str(Path(args.file).resolve()) if args.file else None,
        matrix=info.matrix,
    )
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_play(args) -> int:
    profile = _apply_overrides(profiles.get(args.profile), args)
    source = Path(args.file)
    if not source.is_file():
        print(f"{RED}no such file:{RESET} {source}", file=sys.stderr)
        return 2

    if args.dry_run:
        for arg in player.launch(source, profile, args.mpv_args, dry_run=True):
            print(arg)
        return 0

    info = detect.probe(source)
    display = detect.display_refresh_hz()
    target = profile.resolve_target_fps(info.fps, display)
    multi = profile.multiplier(info.fps, display)

    print(f"\n  {BOLD}{source.name}{RESET}")
    print(f"  {DIM}{info.width}x{info.height} {info.codec} "
          f"{info.fps:.3f} fps -> {target:.2f} fps  (x{multi})  "
          f"via {profile.backend}  [{profile.name}]{RESET}\n")

    if info.fps > profile.bypass_above_fps:
        print(f"  {DIM}source already above {profile.bypass_above_fps:g} fps - "
              f"playing without interpolation{RESET}\n")

    session = player.launch(source, profile, args.mpv_args)
    try:
        return session.wait()
    except KeyboardInterrupt:
        session.process.terminate()
        return 130
    finally:
        session.script.unlink(missing_ok=True)


def cmd_bench(args) -> int:
    profile = _apply_overrides(profiles.get(args.profile), args)
    print(f"\n  benchmarking {Path(args.file).name} with {BOLD}{profile.name}{RESET} "
          f"({profile.backend}) ...\n")
    try:
        result = player.benchmark(args.file, profile, frames=args.frames)
    except RuntimeError as exc:
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return 1

    verdict = (f"{GREEN}real time{RESET}" if result["realtime"]
               else f"{RED}too slow - will stutter{RESET}")
    print(f"  resolution   {result['resolution']}")
    print(f"  source       {result['source_fps']:.3f} fps")
    print(f"  target       {result['target_fps']:.2f} fps")
    print(f"  achieved     {BOLD}{result['achieved_fps']:.2f} fps{RESET}")
    print(f"  headroom     {result['headroom']:.2f}x")
    print(f"  verdict      {verdict}\n")

    if not result["realtime"]:
        # Suggest the cheapest change that would actually close the gap, rather
        # than a fixed list that may include what is already in use.
        tips = [f"twixt tune \"{args.file}\"  "
                "(measures every setting and reports what actually holds)"]
        if profile.grid != "large":
            nxt = "medium" if profile.grid == "small" else "large"
            tips.append(f"--grid {nxt}  (coarser motion search, much cheaper)")
        if profile.backend == "rife" and profile.rife_scale > 0.5:
            tips.append("--profile 4k  (half-resolution flow)")
        if profile.backend != "mvtools":
            tips.append("--backend mvtools  (moves the work off the GPU)")
        print("  try:")
        for tip in tips:
            print(f"    {DIM}{tip}{RESET}")
        print()
    return 0 if result["realtime"] else 1


def cmd_tune(args) -> int:
    """Measure real configurations and report the best one that holds.

    Throughput cannot be extrapolated from a single run: motion estimation is
    charged per source frame pair, so a higher multiplier spreads that cost over
    more output frames and can be *faster* per frame than a lower one. The only
    reliable way to pick settings is to measure each candidate.
    """
    base = _apply_overrides(profiles.get(args.profile), args)
    display = detect.display_refresh_hz()
    info = detect.probe(args.file)

    # A build killed halfway leaves an empty engine that vs-mlrt will later
    # choke on rather than rebuild. Clear those before measuring anything.
    detect.prune_failed_engines()

    multiples = [m for m in (2, 3, 4, 5, 6, 7.5, 8)
                 if info.fps * m <= display + 0.5]

    # Which backend wins is not predictable from first principles -- on some
    # machines the neural path is faster, on others block motion is. Measure
    # whatever is installed unless the caller pinned one.
    #
    # Order matters: mvtools measures in about a second per configuration,
    # while RIFE on TensorRT has to compile an engine for this exact frame size
    # before it can render at all, which takes minutes and prints nothing (the
    # build happens in a trtexec child process whose output benchmark()
    # captures). Measuring the cheap backend first means results start
    # appearing immediately instead of after a silent wait.
    # RIFE stays opt-in. Its engines are compiled per frame size, so having one
    # for a file you tuned yesterday says nothing about this one -- there is no
    # way to know a build is coming until it is already running, and a single
    # row can then cost ten minutes. Asking for it is cheaper than discovering
    # it. An explicit --backend rife still means what it says.
    installed = detect.available_backends()
    if args.backend:
        backends = [args.backend]
    else:
        wanted = ("mvtools", "rife") if args.with_rife else ("mvtools",)
        backends = [b for b in wanted if installed.get(b)]
    if not backends:
        print(f"{RED}No backend installed. Run bootstrap.ps1.{RESET}", file=sys.stderr)
        return 1

    print(f"\n  tuning {Path(args.file).name}  "
          f"{DIM}{info.width}x{info.height} {info.fps:.3f} fps -> {display:.0f} Hz "
          f"panel{RESET}")
    if "rife" in backends:
        print(f"  {DIM}RIFE: TensorRT compiles an engine per frame size, so the "
              f"first {info.width}x{info.height} row can take several minutes{RESET}")
    else:
        print(f"  {DIM}mvtools only -- add --with-rife to measure the neural "
              f"backend too (slow to start){RESET}")
    print()
    print(f"  {'backend':<9} {'grid':<8} {'target':>9} {'achieved':>10}   "
          f"verdict    margin")
    print(f"  {'-' * 62}")

    best = None
    for backend in backends:
        # Grid only affects block motion estimation; sweeping it for the
        # neural backend would just repeat the same measurement.
        if backend == "mvtools":
            grids = [args.grid] if args.grid else ["large", "medium", "small"]
        else:
            grids = [base.grid]

        for grid in grids:
            for multiple in sorted(multiples, reverse=True):
                profile = dataclasses.replace(base)
                profile.backend = backend
                profile.grid = grid
                profile.rate_mode = "fixed"
                profile.rate_fixed = info.fps * multiple
                profile.max_output_fps = display

                target = min(info.fps * multiple, display)
                shown_grid = grid if backend == "mvtools" else "-"
                # Say what is being measured *before* measuring it. A run can
                # take anywhere from a second to several minutes, and a table
                # that only fills in on completion looks identical to a hang.
                _status(f"{backend:<9} {shown_grid:<8} {target:>8.1f}p "
                        f"{'measuring...':>10}")

                started = _time.perf_counter()
                try:
                    r = player.benchmark(args.file, profile, frames=args.frames)
                except RuntimeError:
                    _endstatus(f"  {backend:<9} {shown_grid:<8} {DIM}failed{RESET}")
                    continue
                took = _time.perf_counter() - started

                # A configuration that merely reaches the target on one sample
                # will not survive a busy scene: motion estimation costs more
                # where there is more motion, and the sample is a single point
                # in the film. Demand a margin before calling it playable --
                # measured at 1.11x, this file dropped 94 frames in ten seconds
                # and drifted 125 ms out of sync.
                holds = r["achieved_fps"] >= r["target_fps"] * args.margin
                mark = (f"{GREEN}holds{RESET}    " if holds
                        else f"{DIM}too slow {RESET}")
                _endstatus(f"  {backend:<9} {shown_grid:<8} {r['target_fps']:>8.1f}p "
                           f"{r['achieved_fps']:>9.1f}p   {mark}  "
                           f"{DIM}{r['headroom']:.2f}x  {took:.1f}s{RESET}")

                if holds:
                    if best is None or r["target_fps"] > best[0]["target_fps"]:
                        best = (r, backend, grid, multiple, profile)
                    break   # found this row's ceiling

    print()
    if not best:
        print(f"  {RED}Nothing sustained real time.{RESET} "
              "Try a lower-resolution source.\n")
        return 1

    r, backend, grid, multiple, winner = best
    cap = int(r["target_fps"])
    grid_flag = f" --grid {grid}" if backend == "mvtools" else ""

    # Write the winning settings out, so the answer is a file you can reuse
    # rather than a command line to retype for every file.
    winner.name = f"tuned {info.width}x{info.height} @ {cap}fps"
    winner.description = (
        f"measured on this machine: {r['achieved_fps']:.1f} fps achieved "
        f"against a {r['target_fps']:.1f} fps target"
    )
    # Back to a ceiling rather than a pinned rate, so the profile still does
    # the right thing on a source with a different frame rate.
    winner.rate_mode = "display"
    winner.max_output_fps = float(cap)
    out = Path(args.output) if args.output else Path("twixt-tuned.json")
    winner.save(out)

    print(f"  {BOLD}best: {r['target_fps']:.1f} fps ({multiple:g}x source) "
          f"on {backend}{RESET}")
    print(f"  {DIM}headroom {r['headroom']:.2f}x   saved to {out}{RESET}\n")
    print("  play this file:")
    print(f"    {BOLD}python -m twixt play \"{args.file}\" --backend {backend}"
          f"{grid_flag} --cap {cap}{RESET}")
    print("  or reuse the tuned settings on anything:")
    print(f"    {BOLD}python -m twixt play \"<file>\" --profile {out}{RESET}\n")
    print(f"  {DIM}measured {r['headroom']:.2f}x the target against a required "
          f"{args.margin:g}x. If it still stutters, re-run with a higher "
          f"--margin.{RESET}\n")
    return 0


def cmd_install_mpchc(args) -> int:
    from ._mpchc_cli import install
    return install(args, _apply_overrides)


def cmd_mpchc_status(args) -> int:
    from ._mpchc_cli import status
    return status(args)


def cmd_mpchc_uninstall(args) -> int:
    from ._mpchc_cli import uninstall
    return uninstall(args)


def cmd_gui(args) -> int:
    from .gui import main as gui_main
    return gui_main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="twixt",
        description="Real-time video frame interpolation for playback.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("-p", "--profile", default="film",
                        help="preset name or path to a .json profile")
        sp.add_argument("-b", "--backend", choices=["rife", "mvtools", "svpflow"])
        sp.add_argument("--fps", type=float, help="pin an exact output fps")
        sp.add_argument("--multiplier", help="frame multiplier, e.g. 2 or 5/2")
        sp.add_argument("--cap", type=float, help="maximum output fps")
        sp.add_argument("--grid", choices=["small", "medium", "large"])
        sp.add_argument("--masking",
                        choices=["disabled", "weak", "average", "strong"])

    p_play = sub.add_parser("play", help="play a file with interpolation")
    p_play.add_argument("file")
    add_common(p_play)
    p_play.add_argument("--dry-run", action="store_true",
                        help="print the mpv command instead of running it")
    p_play.add_argument("mpv_args", nargs="*",
                        help="extra arguments passed straight to mpv")
    p_play.set_defaults(func=cmd_play)

    p_bench = sub.add_parser("bench", help="measure whether real time is achievable")
    p_bench.add_argument("file")
    add_common(p_bench)
    p_bench.add_argument("-n", "--frames", type=int, default=240)
    p_bench.set_defaults(func=cmd_bench)

    p_script = sub.add_parser("script", help="print or save the generated .vpy")
    p_script.add_argument("file", nargs="?")
    add_common(p_script)
    p_script.add_argument("-o", "--output")
    p_script.set_defaults(func=cmd_script)

    p_tune = sub.add_parser("tune", help="find the best settings this machine can hold")
    p_tune.add_argument("file")
    add_common(p_tune)
    # Enough frames that scene-to-scene variation averages out; the fixed
    # per-run overhead is subtracted out, so this no longer skews the rate.
    p_tune.add_argument("-n", "--frames", type=int, default=150)
    p_tune.add_argument("--with-rife", action="store_true",
                        help="also measure RIFE, even though TensorRT has to "
                             "compile an engine for this frame size first "
                             "(minutes, once per resolution)")
    p_tune.add_argument("--margin", type=float, default=1.25,
                        help="how much faster than real time a setting must "
                             "measure before it counts as playable "
                             "(default 1.25, i.e. 25%% spare capacity)")
    p_tune.add_argument("-o", "--output", help="where to save the winning "
                                               "profile (default twixt-tuned.json)")
    p_tune.set_defaults(func=cmd_tune)

    p_mpc = sub.add_parser("install-mpchc",
                           help="EXPERIMENTAL: MPC-HC integration (does not work yet, see README)")
    add_common(p_mpc)
    p_mpc.set_defaults(func=cmd_install_mpchc)

    sub.add_parser("mpchc-status",
                   help="check the MPC-HC integration").set_defaults(func=cmd_mpchc_status)

    sub.add_parser("uninstall-mpchc",
                   help="remove the MPC-HC integration").set_defaults(
                       func=cmd_mpchc_uninstall)

    sub.add_parser("doctor", help="check the runtime install").set_defaults(func=cmd_doctor)
    sub.add_parser("profiles", help="list built-in profiles").set_defaults(func=cmd_profiles)
    sub.add_parser("gui", help="open the manager window").set_defaults(func=cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyError as exc:
        print(f"{RED}{exc.args[0]}{RESET}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return 1
