"""Drives the Qt window offscreen. Skips when PySide6 is not installed."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from PySide6.QtCore import QCoreApplication, QElapsedTimer, QEvent
    from PySide6.QtGui import QFocusEvent
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:          # pragma: no cover
    QApplication = None

from twixt import detect   # noqa: E402

FILM = detect.SourceInfo(Path("film.mkv"), fps=23.976, width=2560, height=1440,
                         codec="av1", duration=7200)


def _window():
    from twixt import gui_qt
    app = QApplication.instance() or QApplication([])
    gui_qt._in_thread = lambda fn, *a: None      # no ffprobe / PowerShell here
    w = gui_qt.TwixtWindow()
    w._on_display(180.0)
    w._on_probed(FILM)
    return app, w, gui_qt


def test_cards_and_cadence_readout():
    if QApplication is None:
        print("  (skipped: PySide6 not installed)")
        return
    app, w, gui_qt = _window()
    titles = [b.entry.title for b in w.card_group.buttons()]
    assert "RIFE · GPU" in titles and "Smooth 1440p" in titles and "Film" in titles
    for btn in w.card_group.buttons():
        btn.setChecked(True)
        app.processEvents()
        assert "fps" in w.big_rate.text()
        # Every profile snaps to an even cadence on 180 Hz.
        assert "even" in w.cadence.text() and "uneven" not in w.cadence.text(), \
            (btn.entry.title, w.cadence.text())
    assert w.play_btn.isEnabled() and w.opt_btn.isEnabled()
    w.close()


def test_auto_optimize_runs_and_reports_failure_cleanly():
    if QApplication is None:
        print("  (skipped: PySide6 not installed)")
        return
    app, w, gui_qt = _window()
    gui_qt.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    w.toggle_optimize()
    assert w.proc is not None and not w.play_btn.isEnabled(), "busy while optimizing"
    timer = QElapsedTimer()
    timer.start()
    while w.proc is not None and timer.elapsed() < 60000:
        app.processEvents()
    assert w.proc is None, "tune never finished"
    log = w.opt_log.toPlainText()
    # No runtime on a test box, so `tune` must fail -- and its own message must
    # reach the console, with the stage marked stopped rather than hanging.
    assert log.strip(), "child output never reached the console"
    assert "\x1b[" not in log, "ANSI colour codes leaked into the console"
    assert "stopped" in w.opt_stage.text(), w.opt_stage.text()
    assert w.play_btn.isEnabled(), "Play comes back after the run"
    w.close()


def test_simple_window_auto_custom_and_profiles():
    if QApplication is None:
        print("  (skipped: PySide6 not installed)")
        return
    from twixt import gui_qt
    app = QApplication.instance() or QApplication([])
    gui_qt._in_thread = lambda fn, *a: None
    w = gui_qt.SimpleWindow()
    w.settings.setValue("kind", "auto")
    w.settings.setValue("fps", "auto")
    w._restore_choices()
    w._on_display(180.0)
    assert not w.play_btn.isEnabled(), "nothing to play yet"
    shown = [b.property("fps") for b in w.rate_buttons if not b.isHidden()]
    assert shown == ["auto", 180.0, 90.0, 60.0], shown

    w._on_probed(detect.SourceInfo(Path("[SubsPlease] Frieren - 05 (1080p).mkv"),
                                   fps=23.976, width=1920, height=1080))
    assert w.play_btn.isEnabled()
    plan = w.current_plan()
    assert plan.content == "anime" and plan.fps == 90.0
    assert "Anime" in w.plan_title.text() and w.plan_warn.isHidden()

    # Pick a ready profile instead of Auto.
    next(b for b in w.kind_group.buttons() if b.property("kind") == "sports").setChecked(True)
    assert w.current_plan().content == "sports"

    # Typing a custom number selects Custom and plays exactly that, with a warning.
    w.custom.setValue(w.custom.value())                  # unchanged number...
    QApplication.sendEvent(w.custom.lineEdit(), QFocusEvent(QEvent.FocusIn))
    assert w.custom_btn.isChecked(), "...but clicking into the box selects Custom"
    w.custom.setValue(120)
    plan = w.current_plan()
    assert plan.fps == 120.0 and not plan.profile.snap_to_display
    assert not w.plan_warn.isHidden() and "judder" in w.plan_warn.text()
    w.close()


def test_smooth_rates_per_monitor():
    if QApplication is None:
        return
    from twixt.gui_qt import smooth_rates
    assert smooth_rates(180.0) == [180.0, 90.0, 60.0]
    assert smooth_rates(144.0) == [144.0, 72.0]
    assert smooth_rates(120.0) == [120.0, 60.0]
    assert smooth_rates(60.0) == [60.0]


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
