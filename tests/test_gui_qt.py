"""Drives the Qt window offscreen. Skips when PySide6 is not installed."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from PySide6.QtCore import QCoreApplication, QElapsedTimer
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
