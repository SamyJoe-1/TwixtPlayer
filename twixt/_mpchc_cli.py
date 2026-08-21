"""CLI commands for the MPC-HC integration.

Kept in its own module so `cli.py` stays about argument parsing.
"""

from __future__ import annotations

from pathlib import Path

from . import detect, mpchc, profiles

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def install(args, apply_overrides) -> int:
    """Put interpolation inside MPC-HC so every video is handled automatically."""
    profile = apply_overrides(profiles.get(args.profile), args)

    print()
    print(f"{BOLD}Setting up MPC-HC{RESET}")
    print()

    mpc = mpchc.find_mpc_hc()
    if not mpc:
        print(f"  {RED}MPC-HC not found.{RESET} Install it, run it once, then retry.")
        print()
        return 1
    print(f"  player       {DIM}{mpc}{RESET}")

    backup = mpchc.backup_filters(
        Path(mpchc.ROOT) / "runtime" / "mpchc-filters-backup.reg")
    if backup:
        print(f"  backup       {DIM}{backup}{RESET}")

    script = mpchc.write_player_script(profile)
    print(f"  script       {DIM}{script}{RESET}")

    added = mpchc.ensure_runtime_on_path()
    if added:
        print(f"  PATH         {DIM}added {len(added)} runtime directories{RESET}")

    print(f"  filter       {DIM}registering (accept the admin prompt)...{RESET}")
    ok, detail = mpchc.register_filter_elevated()
    if not ok:
        print()
        print(f"  {RED}Could not register the DirectShow filter:{RESET} {detail}")
        print()
        return 1
    print(f"  filter       {GREEN}{detail}{RESET}")

    clsid = mpchc.real_filter_clsid()
    mpchc.set_script_path(script)
    try:
        where = mpchc.configure_mpc_external_filter(clsid)
    except RuntimeError as exc:
        print()
        print(f"  {RED}{exc}{RESET}")
        print()
        return 1
    print(f"  MPC-HC entry {DIM}{where}{RESET}")

    print()
    print(f"  {GREEN}Done.{RESET} Open any video in MPC-HC and it is interpolated.")
    print(f"  {DIM}Sources already above {profile.bypass_above_fps:g} fps are "
          f"left alone.{RESET}")
    print(f"  {DIM}Change settings later:  python -m twixt install-mpchc "
          f"--profile <name>{RESET}")
    print()
    return 0


def status(args) -> int:
    print()
    print(f"{BOLD}MPC-HC integration{RESET}")
    print()
    rows = mpchc.status()
    width = max(len(n) for n, _, _ in rows)
    for name, ok, detail in rows:
        mark = f"{GREEN}ok{RESET}" if ok else f"{RED}--{RESET}"
        print(f"  [{mark}] {name:<{width}}  {DIM}{detail}{RESET}")
    print()
    return 0


def uninstall(args) -> int:
    print()
    ok, detail = mpchc.unregister_filter_elevated()
    mark = f"{GREEN}removed{RESET}" if ok else f"{RED}{detail}{RESET}"
    print(f"  DirectShow filter  {mark}")
    removed = mpchc.remove_mpc_external_filter()
    print(f"  MPC-HC entry       {DIM}{'removed' if removed else 'none found'}{RESET}")
    print()
    return 0 if ok else 1
