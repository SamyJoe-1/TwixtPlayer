"""The twixt manager window, Qt edition.

Drop a video, pick a profile card, press Play. Or press Auto-optimize and let
`tune` + `check --auto` find the highest rate this machine plays smoothly,
then come back with that profile selected.

PySide6 is an optional dependency (pip install PySide6). Without it,
`twixt gui` falls back to the old Tk window.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QObject, QProcess, QPropertyAnimation,
                            QProcessEnvironment, Qt, QTimer, Signal, Property)
from PySide6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFileDialog,
                               QFrame, QGraphicsDropShadowEffect, QGridLayout,
                               QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from . import detect, player, profiles

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".webm", ".m2ts", ".ts", ".wmv",
             ".flv", ".m4v"}

# --- palette ------------------------------------------------------------------

BG = "#07080c"
PANEL = "#10131b"
PANEL_HI = "#161a25"
LINE = "#232838"
TEXT = "#e8ecf5"
MUTED = "#8a93a8"
CYAN = "#00e5ff"
VIOLET = "#a855f7"
GOOD = "#3ef0a0"
WARN = "#ffb547"
BAD = "#ff5c7a"

QSS = f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif; color: {TEXT}; }}
QMainWindow, #root {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#section {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 2px; }}
QLabel#chip {{
    background: {PANEL_HI}; border: 1px solid {LINE}; border-radius: 10px;
    padding: 3px 10px; font-size: 12px; color: {MUTED};
}}
QLabel#chipOk {{
    background: rgba(62,240,160,0.08); border: 1px solid rgba(62,240,160,0.35);
    border-radius: 10px; padding: 3px 10px; font-size: 12px; color: {GOOD};
}}
QLabel#chipWarn {{
    background: rgba(255,181,71,0.08); border: 1px solid rgba(255,181,71,0.35);
    border-radius: 10px; padding: 3px 10px; font-size: 12px; color: {WARN};
}}
QFrame#panel {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 16px; }}
QPushButton#ghost {{
    background: transparent; border: 1px solid {LINE}; border-radius: 12px;
    padding: 12px 22px; font-size: 14px; font-weight: 600; color: {TEXT};
}}
QPushButton#ghost:hover {{ border-color: {CYAN}; color: {CYAN}; }}
QPushButton#ghost:disabled {{ color: #4a5163; border-color: #1b1f2b; }}
QPushButton#link {{
    background: transparent; border: none; color: {MUTED}; font-size: 12px; padding: 4px;
}}
QPushButton#link:hover {{ color: {CYAN}; }}
QCheckBox {{ color: {MUTED}; font-size: 12px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 5px; border: 1px solid {LINE}; background: {PANEL_HI};
}}
QCheckBox::indicator:checked {{ background: {CYAN}; border-color: {CYAN}; }}
QPlainTextEdit {{
    background: #05060a; border: 1px solid {LINE}; border-radius: 12px; padding: 8px;
    font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px; color: #b9c2d6;
}}
QProgressBar {{
    background: {PANEL_HI}; border: none; border-radius: 3px; height: 6px; max-height: 6px;
}}
QProgressBar::chunk {{
    border-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {CYAN}, stop:1 {VIOLET});
}}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; }}
QScrollBar::handle:horizontal {{ background: {LINE}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QMessageBox {{ background: {PANEL}; }}
"""

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _glow(widget: QWidget, color: str, radius: int = 32, alpha: int = 150) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    c = QColor(color)
    c.setAlpha(alpha)
    effect.setColor(c)
    effect.setBlurRadius(radius)
    effect.setOffset(0, 0)
    widget.setGraphicsEffect(effect)


def _dark_title_bar(window: QWidget) -> None:
    """Ask DWM for a dark title bar (DWMWA_USE_IMMERSIVE_DARK_MODE = 20).

    Documented by Microsoft for Windows 11 / Windows 10 20H1+. Anything older
    just ignores the attribute.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(window.winId()), 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


# --- background work ----------------------------------------------------------

class _Bridge(QObject):
    """Carries results from worker threads back onto the GUI thread."""
    probed = Signal(object)
    display = Signal(float)
    launched = Signal(object)
    failed = Signal(str)


def _in_thread(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


# --- widgets ------------------------------------------------------------------

class GradientButton(QPushButton):
    """The primary action: neon gradient pill with a breathing glow."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(52)
        self.setMinimumWidth(200)
        f = self.font()
        f.setPointSize(13)
        f.setWeight(QFont.DemiBold)
        self.setFont(f)
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 0)
        self._glow.setColor(QColor(0, 229, 255, 160))
        self._glow.setBlurRadius(28)
        self.setGraphicsEffect(self._glow)
        self._pulse = QPropertyAnimation(self, b"glowRadius", self)
        self._pulse.setDuration(1600)
        self._pulse.setStartValue(18.0)
        self._pulse.setKeyValueAt(0.5, 44.0)
        self._pulse.setEndValue(18.0)
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.InOutSine)

    def _get_radius(self) -> float:
        return self._glow.blurRadius()

    def _set_radius(self, value: float) -> None:
        self._glow.setBlurRadius(value)

    glowRadius = Property(float, _get_radius, _set_radius)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        # No glow while disabled: a lit-up button that does nothing reads as broken.
        if hasattr(self, "_glow"):
            self._glow.setEnabled(self.isEnabled())

    def set_pulsing(self, on: bool) -> None:
        if on and self.isEnabled():
            self._pulse.start()
        else:
            self._pulse.stop()
            self._glow.setBlurRadius(28)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        grad = QLinearGradient(r.topLeft(), r.topRight())
        if self.isEnabled():
            grad.setColorAt(0, QColor(CYAN))
            grad.setColorAt(1, QColor(VIOLET))
        else:
            grad.setColorAt(0, QColor("#1b2030"))
            grad.setColorAt(1, QColor("#1b2030"))
        if self.isDown():
            p.setOpacity(0.85)
        elif self.underMouse():
            p.setOpacity(0.95)
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setOpacity(1.0)
        p.setPen(QColor("#04060a") if self.isEnabled() else QColor("#4a5163"))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignCenter, self.text())


class DropZone(QFrame):
    """Big target for a dropped file; click to browse."""

    dropped = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(190)
        self._hot = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(10)
        self._lay = lay
        self.icon = QLabel("⬇")
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setStyleSheet(f"font-size: 34px; color: {CYAN};")
        self.title = QLabel("Drop a video here")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.hint = QLabel("or click to browse  ·  mkv  mp4  webm  mov  m2ts")
        self.hint.setObjectName("muted")
        self.hint.setAlignment(Qt.AlignCenter)
        self.chips = QHBoxLayout()
        self.chips.setAlignment(Qt.AlignCenter)
        self.chips.setSpacing(8)
        lay.addStretch(1)
        lay.addWidget(self.icon)
        lay.addWidget(self.title)
        lay.addWidget(self.hint)
        lay.addLayout(self.chips)
        lay.addStretch(1)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.6) % 24
        self.update()

    def set_compact(self, on: bool) -> None:
        """Shrink to a one-line bar so the optimize console gets the room."""
        self.icon.setVisible(not on)
        self.hint.setVisible(not on)
        self._lay.setContentsMargins(28, 10 if on else 24, 28, 12 if on else 24)
        self._lay.setSpacing(6 if on else 10)
        self.setMinimumHeight(100 if on else 190)
        self.setMaximumHeight(110 if on else 16777215)

    def show_file(self, name: str, chips: list[tuple[str, str]]) -> None:
        self.icon.setText("▶")
        self.title.setText(name)
        self.hint.setText("drop another file or click to change")
        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for text, kind in chips:
            chip = QLabel(text)
            chip.setObjectName(kind)
            self.chips.addWidget(chip)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(2, 2, -2, -2)
        p.setBrush(QColor(PANEL_HI if self._hot else PANEL))
        pen = QPen(QColor(CYAN if self._hot else "#2b3246"))
        pen.setWidthF(2.0 if self._hot else 1.5)
        pen.setStyle(Qt.CustomDashLine)
        pen.setDashPattern([6, 6])
        pen.setDashOffset(self._phase if self._hot else 0)
        p.setPen(pen)
        p.drawRoundedRect(r, 18, 18)

    def mousePressEvent(self, _event) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open video", "",
            "Video files (" + " ".join("*" + e for e in sorted(VIDEO_EXT)) + ");;All files (*.*)")
        if path:
            self.dropped.emit(path)

    def _local(self, event) -> str | None:
        if not event.mimeData().hasUrls():
            return None
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and Path(path).suffix.lower() in VIDEO_EXT:
                return path
        return None

    def dragEnterEvent(self, event) -> None:
        if self._local(event):
            self._hot = True
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, _event) -> None:
        self._hot = False
        self.update()

    def dropEvent(self, event) -> None:
        self._hot = False
        self.update()
        path = self._local(event)
        if path:
            self.dropped.emit(path)


@dataclass
class ProfileEntry:
    key: str
    title: str
    tag: str
    blurb: str
    profile: profiles.Profile


class ProfileCard(QPushButton):
    def __init__(self, entry: ProfileEntry, parent=None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(220, 128)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)
        top = QHBoxLayout()
        title = QLabel(entry.title)
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        top.addWidget(title)
        top.addStretch(1)
        lay.addLayout(top)
        tag = QLabel(entry.tag)
        tag.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {CYAN};")
        tag.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(tag)
        blurb = QLabel(entry.blurb)
        blurb.setWordWrap(True)
        blurb.setObjectName("muted")
        blurb.setStyleSheet("font-size: 12px;")
        blurb.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(blurb)
        lay.addStretch(1)
        self.setToolTip(entry.profile.description or entry.blurb)
        self.toggled.connect(self._restyle)
        self._restyle(False)

    def _restyle(self, on: bool) -> None:
        if on:
            self.setStyleSheet(
                f"QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                f"stop:0 rgba(0,229,255,0.14), stop:1 rgba(168,85,247,0.14));"
                f" border: 1.5px solid {CYAN}; border-radius: 16px; }}")
            _glow(self, CYAN, 26, 110)
        else:
            self.setStyleSheet(
                f"QPushButton {{ background: {PANEL}; border: 1px solid {LINE};"
                f" border-radius: 16px; }} QPushButton:hover {{ border-color: #3a4260; }}")
            self.setGraphicsEffect(None)


def _discover_profiles() -> list[ProfileEntry]:
    """Optimized result first, then the tuned json files, then presets."""
    root = detect.ROOT
    entries: list[ProfileEntry] = []

    def add_json(name, title, tag, blurb):
        path = root / name
        if path.is_file():
            try:
                entries.append(ProfileEntry(str(path), title, tag, blurb, profiles.Profile.load(path)))
            except Exception:
                pass

    add_json("twixt-checked.json", "Optimized", "★ MEASURED ON THIS PC",
             "Highest rate Auto-optimize played with zero drops.")
    add_json("twixt-1440p-rife.json", "RIFE · GPU", "NEURAL  ·  TENSORRT",
             "Least ghosting on fast motion. Runs on the RTX card.")
    add_json("twixt-1440p.json", "Smooth 1440p", "MVTOOLS  ·  CPU",
             "Block motion on the CPU. Leaves the GPU free.")
    add_json("twixt-1440p-120hz.json", "120 Hz panel", "MVTOOLS  ·  5× FILM",
             "Only with the monitor set to 120 Hz.")
    add_json("twixt-enhance.json", "Upscale + smooth", "REALESRGAN  ·  MVTOOLS",
             "GPU 2× upscale for 720p/1080p sources.")
    add_json("twixt-tuned.json", "Last tune", "VSPIPE ESTIMATE",
             "Settings from the last `twixt tune`.")
    for key, title, tag in (("film", "Film", "PRESET"), ("anime", "Anime", "PRESET"),
                            ("sports", "Sports", "PRESET"), ("neural", "Neural", "PRESET")):
        prof = profiles.get(key)
        entries.append(ProfileEntry(key, title, tag, prof.description.split(".")[0] + ".", prof))
    return entries


# --- main window ----------------------------------------------------------------

class TwixtWindow(QMainWindow):
    def __init__(self, initial: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("twixt")
        self.resize(1080, 760)
        self.setMinimumSize(900, 680)

        self.source: detect.SourceInfo | None = None
        self.display_fps = 0.0
        self.session: player.Session | None = None
        self.proc: QProcess | None = None
        self._stage = ""
        self._bridge = _Bridge()
        self._bridge.probed.connect(self._on_probed)
        self._bridge.display.connect(self._on_display)
        self._bridge.launched.connect(self._on_launched)
        self._bridge.failed.connect(self._on_failed)

        self._build()
        self._load_cards()
        _in_thread(lambda: self._bridge.display.emit(detect.display_refresh_hz()))
        self._watch = QTimer(self)
        self._watch.timeout.connect(self._poll_session)
        self._watch.start(500)
        if initial:
            self.open_file(initial)

    # --- layout ----------------------------------------------------------

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(32, 26, 32, 22)
        outer.setSpacing(18)

        # header
        head = QHBoxLayout()
        logo = QLabel(
            f'<span style="font-size:30px;font-weight:800;color:{CYAN}">twi</span>'
            f'<span style="font-size:30px;font-weight:800;color:{VIOLET}">xt</span>')
        _glow(logo, CYAN, 24, 90)
        sub = QLabel("real-time frame interpolation")
        sub.setObjectName("muted")
        sub.setStyleSheet("font-size: 13px; padding-top: 10px;")
        head.addWidget(logo)
        head.addSpacing(10)
        head.addWidget(sub)
        head.addStretch(1)
        self.chip_display = QLabel("display …")
        self.chip_display.setObjectName("chip")
        head.addWidget(self.chip_display)
        backends = detect.available_backends()
        for name, label in (("mvtools", "MVTools"), ("rife", "RIFE GPU")):
            chip = QLabel(("● " if backends.get(name) else "○ ") + label)
            chip.setObjectName("chipOk" if backends.get(name) else "chip")
            chip.setToolTip("installed" if backends.get(name) else "not installed - run bootstrap.ps1")
            head.addWidget(chip)
        outer.addLayout(head)

        # drop zone
        self.drop = DropZone()
        self.drop.dropped.connect(self.open_file)
        outer.addWidget(self.drop)

        # profiles
        sec = QLabel("PROFILE")
        sec.setObjectName("section")
        outer.addWidget(sec)
        self.cards_host = QWidget()
        self.cards_row = QHBoxLayout(self.cards_host)
        self.cards_row.setContentsMargins(4, 4, 4, 10)
        self.cards_row.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidget(self.cards_host)
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(168)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        self.card_group = QButtonGroup(self)
        self.card_group.setExclusive(True)
        self.card_group.buttonToggled.connect(lambda *_: self._refresh_summary())

        # summary panel
        panel = QFrame()
        panel.setObjectName("panel")
        pl = QGridLayout(panel)
        pl.setContentsMargins(22, 18, 22, 18)
        pl.setHorizontalSpacing(28)
        self.big_rate = QLabel("— fps")
        self.big_rate.setStyleSheet("font-size: 34px; font-weight: 800;")
        self.rate_detail = QLabel("drop a video to see what will play")
        self.rate_detail.setObjectName("muted")
        self.cadence = QLabel("")
        self.cadence.setObjectName("chip")
        self.cadence.hide()
        left = QVBoxLayout()
        left.addWidget(self.big_rate)
        left.addWidget(self.rate_detail)
        pl.addLayout(left, 0, 0)
        pl.addWidget(self.cadence, 0, 1, Qt.AlignRight | Qt.AlignVCenter)
        outer.addWidget(panel)

        # actions
        actions = QHBoxLayout()
        actions.setSpacing(14)
        self.play_btn = GradientButton("▶  Play")
        self.play_btn.clicked.connect(self.toggle_play)
        self.opt_btn = QPushButton("⚡  Auto-optimize")
        self.opt_btn.setObjectName("ghost")
        self.opt_btn.setCursor(Qt.PointingHandCursor)
        self.opt_btn.clicked.connect(self.toggle_optimize)
        self.with_rife = QCheckBox("include RIFE (GPU) — first run builds a TensorRT engine")
        self.with_rife.setChecked(bool(backends.get("rife")))
        self.with_rife.setEnabled(bool(backends.get("rife")))
        actions.addWidget(self.play_btn)
        actions.addWidget(self.opt_btn)
        actions.addSpacing(8)
        actions.addWidget(self.with_rife)
        actions.addStretch(1)
        outer.addLayout(actions)

        # optimize console
        self.opt_panel = QFrame()
        self.opt_panel.setObjectName("panel")
        ol = QVBoxLayout(self.opt_panel)
        ol.setContentsMargins(18, 14, 18, 14)
        ol.setSpacing(10)
        self.opt_stage = QLabel("")
        self.opt_stage.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.opt_bar = QProgressBar()
        self.opt_bar.setTextVisible(False)
        self.opt_bar.setRange(0, 0)
        self.opt_log = QPlainTextEdit()
        self.opt_log.setReadOnly(True)
        self.opt_log.setMinimumHeight(170)
        ol.addWidget(self.opt_stage)
        ol.addWidget(self.opt_bar)
        ol.addWidget(self.opt_log, 1)
        self.opt_panel.hide()
        outer.addWidget(self.opt_panel, 1)

        outer.addStretch(0)
        foot = QHBoxLayout()
        self.status = QLabel("ready")
        self.status.setObjectName("muted")
        self.status.setStyleSheet("font-size: 12px;")
        foot.addWidget(self.status)
        foot.addStretch(1)
        keys = QLabel("in player:  TAB  interpolation on/off   ·   BS  live fps")
        keys.setObjectName("muted")
        keys.setStyleSheet("font-size: 12px;")
        foot.addWidget(keys)
        outer.addLayout(foot)
        self._refresh_buttons()

    def _load_cards(self, select: str | None = None) -> None:
        for btn in list(self.card_group.buttons()):
            self.card_group.removeButton(btn)
            btn.deleteLater()
        while self.cards_row.count():
            item = self.cards_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        entries = _discover_profiles()
        first = None
        for entry in entries:
            card = ProfileCard(entry)
            self.card_group.addButton(card)
            self.cards_row.addWidget(card)
            if (select and entry.key == select) or (not select and first is None):
                first = card
        self.cards_row.addStretch(1)
        if first:
            first.setChecked(True)

    # --- state -----------------------------------------------------------

    def current(self) -> ProfileEntry | None:
        btn = self.card_group.checkedButton()
        return btn.entry if btn else None

    def _refresh_buttons(self) -> None:
        busy = self.proc is not None
        playing = self.session is not None
        self.play_btn.setText("■  Stop" if playing else "▶  Play")
        self.play_btn.setEnabled(self.source is not None and not busy)
        self.play_btn.set_pulsing(self.source is not None and not busy and not playing)
        self.opt_btn.setText("✕  Cancel" if busy else "⚡  Auto-optimize")
        self.opt_btn.setEnabled(self.source is not None and not playing)
        self.play_btn.update()

    def _refresh_summary(self) -> None:
        entry = self.current()
        if not entry or not self.source or self.display_fps <= 0:
            if entry:
                self.rate_detail.setText(f"{entry.title}  ·  {entry.profile.backend}"
                                         "  ·  drop a video to see what will play")
            return
        prof, info = entry.profile, self.source
        target = prof.resolve_target_fps(info.fps, self.display_fps)
        multi = prof.multiplier(info.fps, self.display_fps)
        bypass = info.fps > prof.bypass_above_fps or multi <= 1
        if bypass:
            self.big_rate.setText(f"{info.fps:.3g} fps")
            self.rate_detail.setText("source is already high frame rate — plays untouched")
            self.cadence.hide()
            return
        self.big_rate.setText(
            f'<span style="color:{MUTED}">{info.fps:.3f}</span>'
            f'<span style="color:{MUTED}">  →  </span>'
            f'<span style="color:{CYAN}">{target:.4g}</span> fps')
        self.rate_detail.setText(
            f"×{float(multi):.3g}  ({multi})   ·   {prof.backend}   ·   {entry.title}"
            f"   ·   {self.display_fps:g} Hz panel")
        ratio = self.display_fps / target if target else 0
        even = ratio > 0 and abs(ratio - round(ratio)) < 0.02
        self.cadence.setText(f"each frame held {ratio:.3g} refreshes"
                             + ("  ·  even" if even else "  ·  uneven = judder"))
        self.cadence.setObjectName("chipOk" if even else "chipWarn")
        # Object-name selectors only re-apply after a re-polish.
        self.cadence.style().unpolish(self.cadence)
        self.cadence.style().polish(self.cadence)
        self.cadence.show()

    def _set_status(self, text: str, color: str = MUTED) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"font-size: 12px; color: {color};")

    # --- file ------------------------------------------------------------

    def open_file(self, path: str) -> None:
        self._set_status(f"reading {Path(path).name} …")
        self.drop.show_file(Path(path).name, [("reading…", "chip")])
        _in_thread(lambda: self._bridge.probed.emit(detect.probe(path)))

    def _on_probed(self, info: detect.SourceInfo) -> None:
        self.source = info
        mins = int(info.duration // 60)
        chips = [(f"{info.width}×{info.height}" if info.width else "size ?", "chip"),
                 (info.codec.upper() or "codec ?", "chip"),
                 (f"{info.fps:.3f} fps", "chip")]
        if info.duration:
            chips.append((f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 else f"{mins} min", "chip"))
        self.drop.show_file(info.path.name, chips)
        self._set_status("ready")
        self._refresh_summary()
        self._refresh_buttons()

    def _on_display(self, hz: float) -> None:
        self.display_fps = hz
        self.chip_display.setText(f"▣ {hz:g} Hz")
        self._refresh_summary()

    # --- play ------------------------------------------------------------

    def toggle_play(self) -> None:
        if self.session:
            self.session.command("quit")
            return
        entry = self.current()
        if not entry or not self.source:
            return
        if not detect.mpv_path():
            QMessageBox.warning(self, "twixt", "mpv is not installed.\n\nRun bootstrap.ps1 first.")
            return
        self.play_btn.setEnabled(False)
        self._set_status("starting player …", CYAN)
        src, prof = str(self.source.path), entry.profile

        def work():
            try:
                self._bridge.launched.emit(player.launch(src, prof))
            except Exception as exc:          # surfaced in the GUI, not swallowed
                self._bridge.failed.emit(str(exc))
        _in_thread(work)

    def _on_launched(self, session: player.Session) -> None:
        self.session = session
        self._set_status(f"playing at {session.target_fps:.4g} fps — TAB compares with the original", GOOD)
        self._refresh_buttons()

    def _on_failed(self, message: str) -> None:
        self._set_status("could not start", BAD)
        self._refresh_buttons()
        QMessageBox.critical(self, "twixt", message)

    def _poll_session(self) -> None:
        if self.session and self.session.process.poll() is not None:
            self.session.script.unlink(missing_ok=True)
            self.session.script.with_suffix(".conf").unlink(missing_ok=True)
            self.session = None
            self._set_status("ready")
            self._refresh_buttons()

    # --- auto-optimize ---------------------------------------------------

    def toggle_optimize(self) -> None:
        if self.proc:
            self.proc.kill()
            return
        if self.opt_panel.isVisible():
            # Second press after a finished run: close the console.
            self.opt_panel.hide()
            self.drop.set_compact(False)
        entry = self.current()
        if not entry or not self.source:
            return
        reply = QMessageBox.question(
            self, "Auto-optimize",
            "Step 1 measures the filter with vspipe (no window).\n"
            "Step 2 PLAYS the video fullscreen at each candidate rate and reads "
            "mpv's own dropped-frame and cadence counters.\n\n"
            "Don't touch the mouse or keyboard during step 2. Start?")
        if reply != QMessageBox.Yes:
            return
        self.opt_log.clear()
        self.drop.set_compact(True)
        self.opt_panel.show()
        screen = self.screen().availableGeometry().height() if self.screen() else 1000
        if self.height() < 900 and screen >= 960:
            self.resize(self.width(), 920)
        self._queue = [
            ("1 / 2  measuring the filter (vspipe)",
             ["tune", str(self.source.path), "-p", entry.key, "-o", "twixt-tuned.json"]
             + (["--with-rife"] if self.with_rife.isChecked() else [])),
            ("2 / 2  real playback check — hands off",
             ["check", str(self.source.path), "--profile", "twixt-tuned.json",
              "--auto", "-o", "twixt-checked.json"]),
        ]
        self._next_stage()

    def _next_stage(self) -> None:
        if not self._queue:
            self.proc = None
            self.opt_stage.setText("✓  done — Optimized profile selected")
            self.opt_stage.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {GOOD};")
            self.opt_bar.setRange(0, 1)
            self.opt_bar.setValue(1)
            self._load_cards(select=str(detect.ROOT / "twixt-checked.json"))
            self._set_status("optimized profile saved to twixt-checked.json", GOOD)
            self._refresh_buttons()
            return
        label, args = self._queue.pop(0)
        self.opt_stage.setText(label)
        self.opt_stage.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.opt_bar.setRange(0, 0)
        proc = QProcess(self)
        proc.setWorkingDirectory(str(detect.ROOT))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._read_proc)
        proc.finished.connect(self._proc_done)
        self.proc = proc
        self._refresh_buttons()
        proc.start(sys.executable, ["-m", "twixt", *args])

    def _read_proc(self) -> None:
        if not self.proc:
            return
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        text = _ANSI.sub("", text).replace("\r", "\n")
        for line in text.splitlines():
            if line.strip():
                self.opt_log.appendPlainText(line.rstrip())

    def _proc_done(self, code: int, _status) -> None:
        self._read_proc()
        if code != 0:
            self._queue = []
            self.proc = None
            self.opt_stage.setText("✕  stopped — see the log above")
            self.opt_stage.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {BAD};")
            self.opt_bar.setRange(0, 1)
            self.opt_bar.setValue(0)
            self._load_cards(select=self.current().key if self.current() else None)
            self._set_status("auto-optimize did not finish", BAD)
            self._refresh_buttons()
            return
        self._next_stage()

    def closeEvent(self, event) -> None:
        if self.proc:
            self.proc.kill()
        if self.session:
            self.session.command("quit")
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    if "Segoe UI Variable Text" not in QFontDatabase.families():
        app.setFont(QFont("Segoe UI", 10))
    win = TwixtWindow(argv[0] if argv else None)
    win.show()
    _dark_title_bar(win)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
