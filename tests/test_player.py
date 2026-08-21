"""Covers the launcher: mpv argument construction, path quoting, IPC naming."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from twixt import detect, player, profiles   # noqa: E402

SRC = detect.SourceInfo(Path(r"C:\movies\Some Movie (2019).mkv"),
                        fps=23.976, width=1920, height=1080)
SCRIPT = Path(r"C:\Temp\twixt\gen.vpy")
IPC = r"\\.\pipe\twixt-deadbeef"


def _args(profile=None, **kw):
    return player.build_args(SRC, profile or profiles.get("film"), SCRIPT, IPC, **kw)


def test_windows_path_is_length_quoted_not_backslash_escaped():
    vf = next(a for a in _args() if a.startswith("--vf="))
    text = str(SCRIPT)
    assert f"%{len(text.encode())}%{text}" in vf
    assert "\\:" not in vf, "length-quoting makes colon escaping unnecessary"


def test_length_prefix_matches_utf8_bytes_not_characters():
    unicode_path = Path(r"C:\vidéos\日本語\gen.vpy")
    quoted = player._escape(unicode_path)
    declared = int(quoted.split("%")[1])
    assert declared == len(str(unicode_path).encode("utf-8"))
    assert declared != len(str(unicode_path)), "this path must expose the difference"


def test_hardware_decoding_is_disabled():
    # The vapoursynth filter cannot read GPU surfaces; leaving hwdec on is a
    # silent black-screen failure.
    assert "--hwdec=no" in _args()


def test_sync_flags_prevent_the_judder_we_are_removing():
    args = _args()
    assert "--video-sync=display-resample" in args
    assert "--interpolation=no" in args, "mpv blending would fight our filter"


def test_concurrency_follows_profile_streams():
    profile = profiles.get("film")
    profile.num_streams = 3
    vf = next(a for a in _args(profile) if a.startswith("--vf="))
    assert "concurrent-frames=3" in vf
    assert "buffered-frames=6" in vf


def test_extra_args_are_appended_last():
    args = _args(extra=["--start=10:00", "--mute=yes"])
    assert args[-2:] == ["--start=10:00", "--mute=yes"]


def test_source_path_is_passed_verbatim():
    assert str(SRC.path) in _args()


def test_launch_dry_run_works_without_runtime_installed():
    here = Path(__file__)
    args = player.launch(here, profiles.get("film"), dry_run=True)
    assert args[0].endswith("mpv") or args[0].endswith("mpv.exe")
    assert any("vapoursynth=file=" in a for a in args if a.startswith("--vf="))


def test_ipc_name_is_a_valid_named_pipe_on_windows():
    if os.name != "nt":
        return
    args = player.launch(Path(__file__), profiles.get("film"), dry_run=True)
    ipc = next(a for a in args if a.startswith("--input-ipc-server="))
    value = ipc.split("=", 1)[1]
    assert value.startswith("\\\\.\\pipe\\"), f"bad pipe name: {value!r}"


def test_generated_script_is_cleaned_up_by_dry_run_path(tmp=None):
    # dry_run still writes the script so it can be inspected; make sure it exists
    # and is real Python rather than a placeholder.
    import ast
    player.launch(Path(__file__), profiles.get("film"), dry_run=True)
    scripts = sorted(player.SCRIPT_DIR.glob("*.vpy"))
    assert scripts, "dry run should leave an inspectable script"
    ast.parse(scripts[-1].read_text(encoding="utf-8"))
    for s in scripts:
        s.unlink(missing_ok=True)


def test_filter_is_labelled_so_it_can_be_toggled():
    vf = next(a for a in _args() if a.startswith("--vf="))
    assert vf.startswith("--vf=@twixt:vapoursynth="), vf


def test_input_conf_binds_a_toggle_with_the_full_filter_definition():
    import tempfile
    conf = Path(tempfile.gettempdir()) / "twixt-test-input.conf"
    player.write_input_conf(SCRIPT, profiles.get("film"), conf)
    text = conf.read_text(encoding="utf-8")
    conf.unlink(missing_ok=True)
    assert "TAB vf toggle @twixt:vapoursynth=" in text
    # mpv can only re-add a filter it has the definition for.
    assert str(SCRIPT) in text


def test_launch_writes_an_input_conf_next_to_the_script():
    args = player.launch(Path(__file__), profiles.get("film"), dry_run=True)
    conf = next(a for a in args if a.startswith("--input-conf="))
    path = Path(conf.split("=", 1)[1])
    assert path.is_file()
    for leftover in player.SCRIPT_DIR.glob("*"):
        leftover.unlink(missing_ok=True)


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
