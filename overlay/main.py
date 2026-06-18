"""
CivAdvisor Overlay — PySide6 edition (compact pop-up)
=====================================================

A small, draggable, always-on-top pop-up that watches Civ VI's Lua.log, runs
the local advisor engine, and shows:

  • a headline recommendation
  • compact victory-progress bars across all five paths
  • a short, ranked list of the most relevant tip cards

Appears with a Wispr-style pulse-up, sizes itself to its content, remembers
where you drag it, and updates live mid-turn. No network, can't be rate-limited.
"""

import sys
import os
import re
import json
import time
import logging
import threading
import traceback
import collections
import itertools
from datetime import datetime


def first_sentence(text: str) -> str:
    """Terse: the first sentence only (full text lives in the card's tooltip)."""
    t = (text or "").strip()
    m = re.match(r"^(.*?[.!?])(\s|$)", t)
    return m.group(1) if m else t


# ── Writable data directory ──────────────────────────────────────────────────

def _data_dir() -> str:
    """Per-user writable dir for logs and settings.

    The frozen build installs under Program Files, which is read-only for
    normal users, so writable state must live elsewhere. In dev, keep files
    next to the source for convenience.
    """
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "CivAdvisor")
    else:
        d = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(d, exist_ok=True)
    return d


# ── Logging ─────────────────────────────────────────────────────────────────
#
# Set up logging BEFORE importing PySide6 so that a failed Qt import (a missing
# DLL in a frozen build, say) still lands in the log file instead of vanishing
# silently behind the windowed `--noconsole` executable.

class RingLogHandler(logging.Handler):
    """Thread-safe in-memory ring buffer feeding the live log viewer.

    Handlers fire from any thread (the LogWatcher logs too), so the GUI polls
    `since()` on the main thread rather than touching widgets from here.
    """

    def __init__(self, capacity: int = 8000):
        super().__init__()
        self._lock  = threading.Lock()
        self._lines = collections.deque(maxlen=capacity)
        self._base  = 0          # absolute index of the oldest retained line

    def emit(self, record):
        try:
            msg = self.format(record)
        except Exception:
            return
        with self._lock:
            if len(self._lines) == self._lines.maxlen:
                self._base += 1   # the append below will evict the oldest line
            self._lines.append(msg)

    def since(self, idx: int):
        """Return (new_lines, next_idx) for everything after absolute `idx`."""
        with self._lock:
            total = self._base + len(self._lines)
            start = max(0, idx - self._base)
            return list(itertools.islice(self._lines, start, None)), total


LOG_BUFFER = RingLogHandler()
LOG_DIR = os.path.join(_data_dir(), "logs")


def _setup_logging() -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = os.path.join(LOG_DIR, f"civadvisor_{ts}.log")

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  [%(threadName)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    logger = logging.getLogger("civadvisor")
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Live in-app viewer gets everything, down to DEBUG.
    LOG_BUFFER.setLevel(logging.DEBUG)
    LOG_BUFFER.setFormatter(fmt)
    logger.addHandler(LOG_BUFFER)

    # In a windowed (`--noconsole`) build sys.stdout is None; only attach the
    # console handler when there's a real stream to write to.
    if sys.stdout is not None:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
        logger.addHandler(ch)
    logger.info(f"CivAdvisor starting — log: {log_path}")
    logger.info(f"Log directory: {LOG_DIR}")
    return logger

log = _setup_logging()

def _handle_uncaught(exc_type, exc_value, exc_tb):
    log.critical("Unhandled exception:\n" +
                 "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
sys.excepthook = _handle_uncaught


try:
    from PySide6.QtCore import (
        Qt, QThread, Signal, QPropertyAnimation, QEasingCurve, QPoint,
        QParallelAnimationGroup, QTimer, QUrl,
    )
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QDesktopServices
    from PySide6.QtWidgets import (
        QApplication, QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout,
        QScrollArea, QGraphicsDropShadowEffect, QPlainTextEdit, QPushButton,
    )
except Exception:
    log.critical("Failed to import PySide6 — is it installed?\n" + traceback.format_exc())
    raise

import advisor_engine
import board


# ── Config ──────────────────────────────────────────────────────────────────

# Civ VI's Lua.log lives in the user's "My Games" folder, NOT %LOCALAPPDATA%.
# Documents can be redirected (OneDrive), and some installs differ, so probe a
# list of candidates and pick the newest one that exists. An explicit override
# via the CIVADVISOR_LUA_LOG env var or the UI config ("logPath") wins.

_LOG_SUBPATH = os.path.join("My Games", "Sid Meier's Civilization VI", "Logs", "Lua.log")


def lua_log_candidates() -> list:
    home  = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", "")
    one   = os.environ.get("OneDrive") or os.path.join(home, "OneDrive")
    cands = [
        os.path.join(home, "Documents", _LOG_SUBPATH),
        os.path.join(one, "Documents", _LOG_SUBPATH),
        os.path.join(home, _LOG_SUBPATH),
    ]
    if local:
        cands.append(os.path.join(
            local, "Firaxis Games", "Sid Meier's Civilization VI", "Logs", "Lua.log"))
    # De-dup, preserving order.
    seen, out = set(), []
    for p in cands:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


STATE_TAG   = "CIV_ADVISOR_STATE:"
TURNEND_TAG = "CIV_ADVISOR_TURNEND"

UI_CFG_PATH = os.path.join(_data_dir(), "civadvisor_ui.json")


# ── Design tokens (Wispr-Flow inspired) ─────────────────────────────────────

CARD_W     = 326          # narrower, lighter footprint
MARGIN     = 22           # room for the drop shadow
SHADOW_R   = 30
TIPS_W     = CARD_W - 46
MAX_SCROLL = 400          # content area caps here, then scrolls
RISE       = 22           # pulse-up travel distance

BG_TOP   = "#1A1A20"
BG_BOT   = "#0D0D11"
CARD_BG2 = "#1C1C24"
CARD_BG3 = "#23232C"
ACCENT   = "#5B8AF5"
WARM     = "#E8874A"
GOOD     = "#3ECF6C"
TEXT_HI  = "#ECECF1"
TEXT_MID = "#9A9AAB"
TEXT_LO  = "#5A5A6C"
BORDER   = "#2A2A35"

# Spinner frames for the "turn is changing" loading state.
SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

VICTORY_COLOURS = {
    "science": "#5B8AF5", "culture": "#C06BE8", "domination": "#E85A6B",
    "religion": "#E8C24A", "diplomacy": "#3ECF6C",
}
TYPE_COLOURS = {"warn": WARM, "good": GOOD, "info": ACCENT}
TYPE_ICONS   = {"warn": "⚠", "good": "✓", "info": "→"}

# Focus selector — short labels for the pill row, full names for tooltips.
FOCUS_OPTIONS = [
    ("auto", "Auto"), ("science", "Sci"), ("culture", "Cult"),
    ("domination", "Dom"), ("religion", "Rel"), ("diplomacy", "Dip"),
]
FOCUS_FULL = {
    "auto": "Let the engine choose your best path",
    "science": "Push a Science victory",
    "culture": "Push a Culture victory",
    "domination": "Push a Domination victory",
    "religion": "Push a Religious victory",
    "diplomacy": "Push a Diplomatic victory",
}


# ── Log watcher thread ──────────────────────────────────────────────────────

class LogWatcher(QThread):
    state_ready = Signal(object)
    turn_ended  = Signal()

    def __init__(self, candidates, override: str = ""):
        super().__init__()
        self.candidates = [override] if override else list(candidates)
        self.override = override
        self.path = None
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _resolve(self):
        """Pick the newest existing candidate, or None if none exist yet."""
        existing = [p for p in self.candidates if p and os.path.exists(p)]
        if not existing:
            return None
        return max(existing, key=lambda p: os.path.getmtime(p))

    @staticmethod
    def _parse_turn(line):
        i = line.find("turn=")
        if i < 0:
            return None
        try:
            return int(line[i + 5:].split()[0])
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _parse_entity(line):
        """Return (tag, dict) for an entity line, else None."""
        for tag in ("P", "C", "U", "R", "S"):
            marker = "CIV_ADVISOR_" + tag + " "
            idx = line.find(marker)
            if idx < 0:
                continue
            s = line[idx + len(marker):]
            a, b = s.find("{"), s.rfind("}")
            if a < 0 or b <= a:
                return None
            try:
                return tag, json.loads(s[a:b + 1])
            except json.JSONDecodeError:
                return None
        return None

    def run(self):
        log.info("Lua.log candidates (newest existing wins):")
        for p in self.candidates:
            log.info(f"    {'[exists] ' if os.path.exists(p) else '[missing] '}{p}")

        pos = 0
        self.path = self._resolve()
        if self.path:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                pos = f.tell()
            log.info(f"Watching Lua.log: {self.path} (at byte {pos})")
        else:
            log.warning("Lua.log not found yet — will keep checking each candidate.")

        buf = None            # current snapshot being assembled
        last_sig = None
        missing_warned = False
        while not self._stop.is_set():
            try:
                if self.path is None or not os.path.exists(self.path):
                    found = self._resolve()
                    if found:
                        self.path = found
                        pos = 0      # read from start to grab the latest snapshot
                        missing_warned = False
                        log.info(f"Lua.log appeared: {self.path} — reading from start")
                    else:
                        if not missing_warned:
                            log.warning("Lua.log still missing. Is Civ VI logging enabled "
                                        "and the mod active? Checked: "
                                        + " | ".join(self.candidates))
                            missing_warned = True
                        time.sleep(2)
                        continue
                with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    lines = f.readlines()
                    pos = f.tell()
                for line in lines:
                    if "CIV_ADVISOR_TURNEND" in line:
                        self.turn_ended.emit()
                        continue
                    if "CIV_ADVISOR_SNAP_BEGIN" in line:
                        buf = {"turn": self._parse_turn(line), "player": {},
                               "cities": [], "units": [], "rivals": [], "cs": [], "raw": []}
                        continue
                    if "CIV_ADVISOR_SNAP_END" in line:
                        if buf is not None:
                            sig = hash(tuple(buf["raw"]))
                            if sig != last_sig:
                                last_sig = sig
                                self._emit_snapshot(buf)
                            buf = None
                        continue
                    if buf is None:
                        continue
                    ent = self._parse_entity(line)
                    if ent is None:
                        continue
                    tag, data = ent
                    buf["raw"].append(line.strip())
                    if   tag == "P": buf["player"] = data
                    elif tag == "C": buf["cities"].append(data)
                    elif tag == "U": buf["units"].append(data)
                    elif tag == "R": buf["rivals"].append(data)
                    elif tag == "S": buf["cs"].append(data)
            except Exception as e:
                log.error(f"LogWatcher error: {e}\n{traceback.format_exc()}")
            time.sleep(0.35)

    def _emit_snapshot(self, buf):
        try:
            state = board.build_state(buf["turn"], buf["player"], buf["cities"],
                                      buf["units"], buf["rivals"], buf["cs"])
            log.info(f"Snapshot: turn {state.get('turn')} / {state.get('civ')} — "
                     f"{len(buf['cities'])} cities, {len(buf['units'])} units, "
                     f"{len(buf['rivals'])} rivals, {len(buf['cs'])} city-states")
            self.state_ready.emit(state)
        except Exception:
            log.error("Snapshot assemble error:\n" + traceback.format_exc())


# ── Stat chip (with loading spinner) ─────────────────────────────────────────

class StatChip(QFrame):
    """A small labelled stat. Shows a spinner glyph while the turn is changing."""

    def __init__(self, label: str, colour: str):
        super().__init__()
        self._colour  = colour
        self._loading = False
        self.setFixedHeight(36)
        self.setStyleSheet(
            f"StatChip{{background:{CARD_BG2};border:1px solid {BORDER};border-radius:8px;}}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(1)

        self._cap = QLabel(label)
        self._cap.setAlignment(Qt.AlignCenter)
        self._cap.setStyleSheet(f"color:{TEXT_LO};font-size:8px;font-weight:700;letter-spacing:0.5px;")

        self._val = QLabel("—")
        self._val.setAlignment(Qt.AlignCenter)
        self._val.setStyleSheet(f"color:{colour};font-size:12px;font-weight:800;")

        lay.addWidget(self._cap)
        lay.addWidget(self._val)

    @property
    def loading(self) -> bool:
        return self._loading

    def set_value(self, text: str):
        self._loading = False
        self._val.setText(text)
        self._val.setStyleSheet(f"color:{self._colour};font-size:12px;font-weight:800;")

    def set_loading(self):
        self._loading = True
        self._val.setStyleSheet(f"color:{TEXT_MID};font-size:12px;font-weight:800;")
        self._val.setText(SPINNER[0])

    def spin(self, frame: int):
        if self._loading:
            self._val.setText(SPINNER[frame % len(SPINNER)])


# ── Compact victory progress bar ────────────────────────────────────────────

class VictoryBar(QWidget):
    def __init__(self, name, pct, colour, strongest, note):
        super().__init__()
        self.setFixedHeight(18)
        self.setToolTip(note)
        self._frac = max(0.0, min(1.0, pct))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        nm = QLabel(("★ " if strongest else "") + name)
        nm.setFixedWidth(80)
        nm.setStyleSheet(
            f"color:{TEXT_HI if strongest else TEXT_MID};font-size:10px;"
            f"font-weight:{'700' if strongest else '500'};")

        self._track = QFrame()
        self._track.setFixedHeight(5)
        self._track.setStyleSheet(f"background:{CARD_BG3};border-radius:2px;")
        self._fill = QFrame(self._track)
        self._fill.setStyleSheet(f"background:{colour};border-radius:2px;")

        pctl = QLabel(f"{int(self._frac*100)}%")
        pctl.setFixedWidth(30)
        pctl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        pctl.setStyleSheet(f"color:{colour};font-size:10px;font-weight:700;")

        lay.addWidget(nm)
        lay.addWidget(self._track, 1)
        lay.addWidget(pctl)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fill.setGeometry(0, 0, int(self._track.width() * self._frac), 5)


# ── Tip card ─────────────────────────────────────────────────────────────────

class TipCard(QFrame):
    def __init__(self, tip: dict):
        super().__init__()
        ttype  = tip.get("type", "info")
        colour = TYPE_COLOURS.get(ttype, ACCENT)
        icon   = TYPE_ICONS.get(ttype, "→")
        self.setFixedWidth(TIPS_W)
        self.setStyleSheet(
            f"TipCard{{background:{CARD_BG2};border:1px solid {BORDER};border-radius:10px;}}")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QFrame()
        bar.setFixedWidth(3)
        bar.setStyleSheet(f"background:{colour};border-top-left-radius:10px;"
                          f"border-bottom-left-radius:10px;")
        outer.addWidget(bar)

        body = QVBoxLayout()
        body.setContentsMargins(12, 10, 12, 11)
        body.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(7)
        ic = QLabel(icon)
        ic.setStyleSheet(f"color:{colour};font-size:12px;font-weight:700;")
        title = QLabel(tip.get("title", ""))
        title.setStyleSheet(f"color:{TEXT_HI};font-size:12px;font-weight:700;")
        title.setWordWrap(True)
        head.addWidget(ic, 0, Qt.AlignTop)
        head.addWidget(title, 1)
        body.addLayout(head)

        full = tip.get("body", "")
        txt = QLabel(first_sentence(full))
        txt.setWordWrap(True)
        txt.setFixedWidth(TIPS_W - 3 - 24)
        txt.setStyleSheet(f"color:{TEXT_MID};font-size:11px;")
        body.addWidget(txt)

        outer.addLayout(body)
        if full:
            self.setToolTip(full)        # full detail on hover


# ── Live log viewer ──────────────────────────────────────────────────────────

class LogViewer(QWidget):
    """Standalone, resizable window that tails the in-memory log buffer live."""

    def __init__(self, buffer: RingLogHandler, log_dir: str):
        super().__init__()
        self._buf = buffer
        self._idx = 0
        self._log_dir = log_dir

        self.setWindowTitle("CivAdvisor — Live Logs")
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.resize(760, 440)
        self.setStyleSheet(f"background:{BG_BOT};")

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        bar = QHBoxLayout()
        title = QLabel("Live logs (DEBUG)")
        title.setStyleSheet(f"color:{TEXT_HI};font-size:12px;font-weight:800;")
        bar.addWidget(title)
        bar.addStretch(1)

        self._autoscroll = True
        self._auto_btn = QPushButton("Auto-scroll: on")
        self._auto_btn.setCursor(Qt.PointingHandCursor)
        self._auto_btn.clicked.connect(self._toggle_autoscroll)
        folder_btn = QPushButton("Open log folder")
        folder_btn.setCursor(Qt.PointingHandCursor)
        folder_btn.clicked.connect(self._open_folder)
        clear_btn = QPushButton("Clear view")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_view)
        btn_css = (f"QPushButton{{color:{TEXT_HI};background:{CARD_BG3};border:1px solid {BORDER};"
                   f"border-radius:7px;padding:4px 10px;font-size:11px;}}"
                   f"QPushButton:hover{{background:{CARD_BG2};}}")
        for b in (self._auto_btn, folder_btn, clear_btn):
            b.setStyleSheet(btn_css)
            bar.addWidget(b)
        root.addLayout(bar)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(20000)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setStyleSheet(
            f"QPlainTextEdit{{background:#0A0A0D;color:{TEXT_HI};border:1px solid {BORDER};"
            f"border-radius:8px;padding:6px;}}")
        self.text.setFont(QFont("Consolas", 9))
        root.addWidget(self.text, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._poll)

    def _toggle_autoscroll(self):
        self._autoscroll = not self._autoscroll
        self._auto_btn.setText(f"Auto-scroll: {'on' if self._autoscroll else 'off'}")

    def _open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._log_dir))

    def _clear_view(self):
        self.text.clear()

    def _poll(self):
        lines, self._idx = self._buf.since(self._idx)
        if not lines:
            return
        sb = self.text.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        self.text.appendPlainText("\n".join(lines))
        if self._autoscroll and at_bottom:
            sb.setValue(sb.maximum())

    def showEvent(self, e):
        super().showEvent(e)
        self._poll()
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())
        if not self._timer.isActive():
            self._timer.start()

    def closeEvent(self, e):
        self._timer.stop()
        super().closeEvent(e)


# ── Main overlay window ─────────────────────────────────────────────────────

class AdvisorWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._visible     = False
        self._user_closed = False
        self._last_turn   = None
        self._anim_group  = None
        self._drag        = None
        self._last_state  = None
        self._last_report = None
        self._tab_counts  = {}
        self._focus_pills = {}
        self._stat_chips  = {}
        self._spin_frame  = 0
        self._log_viewer  = None

        screen = QGuiApplication.primaryScreen().geometry()
        self.scr_w, self.scr_h = screen.width(), screen.height()
        self.win_w = CARD_W + MARGIN * 2
        self.setFixedWidth(self.win_w)

        cfg = self._load_cfg()
        self._pos_x = int(cfg.get("x", self.scr_w - 12 - self.win_w + MARGIN))
        self._pos_y = int(cfg.get("y", 56))
        self._focus = cfg.get("focus", "auto")
        if self._focus not in FOCUS_FULL:
            self._focus = "auto"

        # Spinner timer — drives the loading glyphs while the turn is changing.
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(90)
        self._spin_timer.timeout.connect(self._tick_spinner)

        self._build_ui()
        self._restyle_focus_pills()
        self._render_content()        # show the waiting state immediately
        self._refit()

        override = os.environ.get("CIVADVISOR_LUA_LOG") or cfg.get("logPath", "")
        self.watcher = LogWatcher(lua_log_candidates(), override.strip())
        self.watcher.state_ready.connect(self._on_state)
        self.watcher.turn_ended.connect(self._on_turn_end)
        self.watcher.start()
        log.info(f"UI ready — watcher started (focus={self._focus})")

        # Show the window right away so it's obvious the app is running, even
        # before the game produces any state.
        self.pop_in()

    # ── Config persistence (position + focus) ─────────────────────────────────

    def _load_cfg(self) -> dict:
        try:
            with open(UI_CFG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cfg(self):
        try:
            with open(UI_CFG_PATH, "w", encoding="utf-8") as f:
                json.dump({"x": self._pos_x, "y": self._pos_y, "focus": self._focus}, f)
        except Exception:
            pass

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)

        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setStyleSheet(f"""
            #card {{
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                           stop:0 {BG_TOP}, stop:1 {BG_BOT});
                border: 1px solid {BORDER};
                border-radius: 16px;
            }}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(SHADOW_R)
        shadow.setColor(QColor(0, 0, 0, 190))
        shadow.setOffset(0, 8)
        self.card.setGraphicsEffect(shadow)
        root.addWidget(self.card)

        c = QVBoxLayout(self.card)
        c.setContentsMargins(16, 13, 16, 12)
        c.setSpacing(10)

        # Header
        header = QHBoxLayout()
        brand = QLabel("◆  CivAdvisor")
        brand.setStyleSheet(f"color:{TEXT_HI};font-size:13px;font-weight:800;")
        self.turn_pill = QLabel("")
        self.turn_pill.setStyleSheet(
            f"color:{ACCENT};background:{CARD_BG3};border-radius:9px;"
            f"padding:2px 9px;font-size:10px;font-weight:700;")
        logs = QLabel("Logs")
        logs.setToolTip("Open the live log viewer")
        logs.setStyleSheet(
            f"color:{TEXT_MID};background:{CARD_BG3};border-radius:9px;"
            f"padding:2px 9px;font-size:10px;font-weight:700;")
        logs.setCursor(Qt.PointingHandCursor)
        logs.mousePressEvent = self._on_logs_click
        close = QLabel("✕")
        close.setStyleSheet(f"color:{TEXT_LO};font-size:13px;")
        close.setCursor(Qt.PointingHandCursor)
        close.mousePressEvent = self._on_close_click
        header.addWidget(brand)
        header.addStretch(1)
        header.addWidget(self.turn_pill)
        header.addSpacing(6)
        header.addWidget(logs)
        header.addSpacing(8)
        header.addWidget(close)
        c.addLayout(header)

        # Meta line
        self.meta = QLabel("Waiting for game…")
        self.meta.setStyleSheet(f"color:{TEXT_MID};font-size:10px;")
        c.addWidget(self.meta)

        # Stats row — key per-turn yields; spin while the turn is changing.
        statrow = QHBoxLayout()
        statrow.setSpacing(5)
        self._stat_chips = {}
        for key, label, colour in (
            ("gold",    "GOLD",  WARM),
            ("science", "SCI",   ACCENT),
            ("culture", "CULT",  "#C06BE8"),
            ("faith",   "FAITH", "#E8C24A"),
        ):
            chip = StatChip(label, colour)
            self._stat_chips[key] = chip
            statrow.addWidget(chip, 1)
        c.addLayout(statrow)

        # Tab bar — Now / Plan / Cities
        self._tab = "now"
        self._tab_btns = {}
        tabrow = QHBoxLayout()
        tabrow.setSpacing(5)
        for key, label in (("now", "Now"), ("plan", "Plan"), ("cities", "Cities")):
            b = QLabel(label)
            b.setAlignment(Qt.AlignCenter)
            b.setCursor(Qt.PointingHandCursor)
            b.mousePressEvent = (lambda e, k=key: self._set_tab(k))
            self._tab_btns[key] = b
            tabrow.addWidget(b, 1)
        c.addLayout(tabrow)

        # Content scroll (rebuilt per tab; sized to content)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet(f"""
            QScrollArea {{ border:none; background:transparent; }}
            QScrollBar:vertical {{ background:transparent; width:5px; margin:0; }}
            QScrollBar::handle:vertical {{ background:{BORDER}; border-radius:2px; min-height:28px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
        """)
        self.content_holder = QWidget()
        self.content_holder.setFixedWidth(TIPS_W)
        self.content_holder.setStyleSheet("background:transparent;")
        self.content_box = QVBoxLayout(self.content_holder)
        self.content_box.setContentsMargins(0, 0, 0, 0)
        self.content_box.setSpacing(7)
        self.scroll.setWidget(self.content_holder)
        c.addWidget(self.scroll)

        # Status
        self.status = QLabel("● Waiting for game…")
        self.status.setStyleSheet(f"color:{TEXT_LO};font-size:9px;")
        c.addWidget(self.status)

        self._restyle_tabs()

    # ── Content rendering ─────────────────────────────────────────────────────

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)        # detach NOW so it stops counting toward size
                w.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

    # ── Focus selector ────────────────────────────────────────────────────────

    def _restyle_focus_pills(self):
        for key, pill in self._focus_pills.items():
            selected = (key == self._focus)
            colour = VICTORY_COLOURS.get(key, ACCENT)
            if selected:
                pill.setStyleSheet(
                    f"color:#0E0E12;background:{colour};border-radius:8px;"
                    f"padding:2px 0;font-size:9px;font-weight:800;")
            else:
                pill.setStyleSheet(
                    f"color:{TEXT_MID};background:{CARD_BG3};border-radius:8px;"
                    f"padding:2px 0;font-size:9px;font-weight:600;")

    def _on_focus_click(self, key: str):
        if key == self._focus:
            return
        self._focus = key
        self._save_cfg()
        log.info(f"Focus set to {key}")
        if self._last_state is not None:        # re-render current turn with the new focus
            self._render(self._last_state, self._compute(self._last_state))

    def _compute(self, state: dict) -> dict:
        try:
            return advisor_engine.compute_report(state, self._focus)
        except Exception:
            log.error("Engine error:\n" + traceback.format_exc())
            return {"meta": {}, "headline": "Internal error", "victories": [],
                    "strongest": "", "tips":
                    [{"type": "warn", "title": "Internal error", "body": "Check logs.", "tab": "now"}]}

    # ── Stats + loading spinner ───────────────────────────────────────────────

    def _update_stats(self, state: dict):
        """Fill the stat chips from the latest snapshot, clearing the spinner."""
        def fmt(v):
            try:
                return f"{float(v):.0f}"
            except (TypeError, ValueError):
                return "—"

        gpt = state.get("gpt")
        gold = fmt(state.get("gold"))
        if gpt is not None:
            try:
                gold = f"{float(state.get('gold', 0)):.0f} ({float(gpt):+.0f})"
            except (TypeError, ValueError):
                pass
        self._stat_chips["gold"].set_value(gold)
        self._stat_chips["science"].set_value(fmt(state.get("science")))
        self._stat_chips["culture"].set_value(fmt(state.get("culture")))
        self._stat_chips["faith"].set_value(fmt(state.get("faith")))
        if self._spin_timer.isActive():
            self._spin_timer.stop()

    def _start_loading(self):
        """Put every stat chip into the spinning state."""
        for chip in self._stat_chips.values():
            chip.set_loading()
        self._spin_frame = 0
        if not self._spin_timer.isActive():
            self._spin_timer.start()

    def _tick_spinner(self):
        self._spin_frame += 1
        if any(chip.spin(self._spin_frame) or chip.loading
               for chip in self._stat_chips.values()):
            return
        self._spin_timer.stop()

    def _on_turn_end(self):
        """Local player ended their turn — stay open, show the AIs are moving."""
        log.info("Turn ended — AIs moving, showing loading state")
        self._start_loading()
        self.status.setText("● AIs moving — computing next turn…")
        self.status.setStyleSheet(f"color:{WARM};font-size:9px;")
        self._user_closed = False
        if not self._visible:
            self.pop_in()

    # ── Tabs ──────────────────────────────────────────────────────────────────

    def _set_tab(self, key):
        self._tab = key
        self._restyle_tabs()
        self._render_content()
        if self._visible:
            QTimer.singleShot(0, self._refit)
        else:
            self._refit()

    def _restyle_tabs(self):
        labels = {"now": "Now", "plan": "Plan", "cities": "Cities"}
        for key, b in self._tab_btns.items():
            n = self._tab_counts.get(key, 0)
            b.setText(labels[key] + (f"  {n}" if n else ""))
            if key == self._tab:
                b.setStyleSheet(f"color:{TEXT_HI};background:{CARD_BG3};border-radius:8px;"
                                f"padding:4px 0;font-size:11px;font-weight:800;")
            else:
                b.setStyleSheet(f"color:{TEXT_LO};background:transparent;border-radius:8px;"
                                f"padding:4px 0;font-size:11px;font-weight:600;")

    def _tips_for(self, tab):
        rep = self._last_report or {}
        return [t for t in rep.get("tips", []) if t.get("tab", "plan") == tab][:6]

    # ── State handling ────────────────────────────────────────────────────────

    def _on_state(self, state: dict):
        self._last_state = state
        log.debug(f"State received — turn={state.get('turn')} gold={state.get('gold')} "
                  f"sci={state.get('science')} cul={state.get('culture')} "
                  f"faith={state.get('faith')} cities={len(state.get('cityData', []))}")
        report = self._compute(state)
        log.debug(f"Report — headline={report.get('headline')!r} "
                  f"tips={len(report.get('tips', []))} strongest={report.get('strongest')}")

        turn = state.get("turn")
        new_turn = (turn != self._last_turn)
        self._last_turn = turn
        if new_turn:
            self._tab = "now"        # each new turn, lead with what's urgent

        self._render(state, report)

        if new_turn:
            self._user_closed = False
            self.pop_in()
        elif self._visible:
            self.move(self._pos_x, getattr(self, "_anchor_y", self._pos_y))
        elif not self._user_closed:
            self.pop_in()

    def _render(self, state: dict, report: dict):
        self._last_report = report
        m = report.get("meta", {})
        self.turn_pill.setText(f"Turn {m.get('turn', state.get('turn', '?'))}")
        self.meta.setText(
            f"{advisor_engine.pretty_leader(m.get('leader',''))}  ·  "
            f"{advisor_engine.pretty_leader(m.get('civ',''))}  ·  {m.get('era','')}")

        self._update_stats(state)

        self._tab_counts = {"now": 0, "plan": 0, "cities": 0}
        for t in report.get("tips", []):
            tab = t.get("tab", "plan")
            self._tab_counts[tab] = self._tab_counts.get(tab, 0) + 1
        self._restyle_tabs()
        self._render_content()

        self.status.setText(f"● Updated {time.strftime('%H:%M')}  ·  drag to move")
        self.status.setStyleSheet(f"color:{GOOD};font-size:9px;")

        if self._visible:
            QTimer.singleShot(0, self._refit)
        else:
            self._refit()

    # ── Per-tab content ─────────────────────────────────────────────────────────

    def _muted(self, text):
        l = QLabel(text)
        l.setWordWrap(True)
        l.setFixedWidth(TIPS_W)
        l.setStyleSheet(f"color:{TEXT_LO};font-size:11px;padding:8px 2px;")
        return l

    def _render_content(self):
        self._clear_layout(self.content_box)
        rep = self._last_report or {}
        if self._tab == "now":
            self._build_now(rep)
        elif self._tab == "plan":
            self._build_plan(rep)
        else:
            self._build_cities(rep)
        self.scroll.verticalScrollBar().setValue(0)

    def _build_now(self, rep):
        head = QLabel("✦  " + rep.get("headline", ""))
        head.setWordWrap(True)
        head.setFixedWidth(TIPS_W)
        head.setStyleSheet(f"color:{TEXT_HI};background:{CARD_BG2};border:1px solid {BORDER};"
                           f"border-radius:10px;padding:8px 11px;font-size:12px;font-weight:700;")
        self.content_box.addWidget(head)
        tips = self._tips_for("now")
        if tips:
            for t in tips:
                self.content_box.addWidget(TipCard(t))
        else:
            self.content_box.addWidget(self._muted("Nothing urgent this turn — check Plan and Cities."))

    def _build_plan(self, rep):
        # Focus selector (rebuilt each render)
        self._focus_pills = {}
        fr = QWidget()
        fr.setFixedWidth(TIPS_W)
        flay = QHBoxLayout(fr)
        flay.setContentsMargins(0, 0, 0, 0)
        flay.setSpacing(4)
        fl = QLabel("Focus")
        fl.setStyleSheet(f"color:{TEXT_LO};font-size:9px;font-weight:700;")
        flay.addWidget(fl)
        for key, short in FOCUS_OPTIONS:
            pill = QLabel(short)
            pill.setAlignment(Qt.AlignCenter)
            pill.setCursor(Qt.PointingHandCursor)
            pill.setToolTip(FOCUS_FULL[key])
            pill.mousePressEvent = (lambda e, k=key: self._on_focus_click(k))
            self._focus_pills[key] = pill
            flay.addWidget(pill, 1)
        self.content_box.addWidget(fr)
        self._restyle_focus_pills()

        # Victory progress
        vics = rep.get("victories", [])
        strongest = rep.get("strongest", "")
        if vics:
            lab = QLabel("VICTORY PROGRESS")
            lab.setStyleSheet(f"color:{TEXT_LO};font-size:9px;font-weight:800;letter-spacing:1px;")
            self.content_box.addWidget(lab)
            for v in vics:
                self.content_box.addWidget(VictoryBar(
                    v["name"], v.get("pct", 0.0), VICTORY_COLOURS.get(v["key"], ACCENT),
                    v["key"] == strongest, v.get("note", "")))

        # Strategy tips
        tips = self._tips_for("plan")
        if tips:
            div = QFrame()
            div.setFixedHeight(1)
            div.setFixedWidth(TIPS_W)
            div.setStyleSheet(f"background:{BORDER};")
            self.content_box.addWidget(div)
            for t in tips:
                self.content_box.addWidget(TipCard(t))

    def _build_cities(self, rep):
        tips = self._tips_for("cities")
        if tips:
            for t in tips:
                self.content_box.addWidget(TipCard(t))
        else:
            self.content_box.addWidget(self._muted("No city actions right now — queues and growth look fine."))

    def _refit(self):
        """Size the window to its content; cap the tips area so it never grows huge.

        Order matters: recompute the inner layouts (now that old widgets are
        detached) BEFORE measuring, or the height comes out stale and squishes
        the card on re-render."""
        self.content_box.activate()
        self.content_holder.adjustSize()
        th = self.content_holder.sizeHint().height()
        self.scroll.setFixedHeight(max(20, min(th, MAX_SCROLL)))

        self.card.layout().activate()
        total = self.card.layout().sizeHint().height() + 2 * MARGIN
        total = max(200, min(total, self.scr_h - 40))
        self.setFixedHeight(total)

        # keep on-screen given the (possibly new) height
        y = self._pos_y
        if y + total > self.scr_h - 12:
            y = max(12, self.scr_h - 12 - total)
        self._anchor_y = y
        if self._visible:
            self.move(self._pos_x, y)

    # ── Pulse-up animation ────────────────────────────────────────────────────

    def pop_in(self):
        x, y = self._pos_x, getattr(self, "_anchor_y", self._pos_y)
        if self._visible:
            self.move(x, y)
            return
        self._visible = True
        self.setWindowOpacity(0.0)
        self.move(x, y + RISE)
        self.show()
        self.raise_()
        self._run_anim(QPoint(x, y + RISE), QPoint(x, y), 0.0, 1.0,
                       QEasingCurve.OutBack)

    def pop_out(self):
        if not self._visible:
            return
        self._visible = False
        x, y = self.x(), self.y()
        self._run_anim(QPoint(x, y), QPoint(x, y - 10), 1.0, 0.0,
                       QEasingCurve.OutCubic, hide=True)

    def _run_anim(self, p0, p1, a0, a1, curve, hide=False):
        pos_anim = QPropertyAnimation(self, b"pos")
        pos_anim.setDuration(300)
        pos_anim.setStartValue(p0)
        pos_anim.setEndValue(p1)
        pos_anim.setEasingCurve(curve)

        op_anim = QPropertyAnimation(self, b"windowOpacity")
        op_anim.setDuration(220)
        op_anim.setStartValue(a0)
        op_anim.setEndValue(a1)
        op_anim.setEasingCurve(QEasingCurve.OutCubic)

        grp = QParallelAnimationGroup(self)
        grp.addAnimation(pos_anim)
        grp.addAnimation(op_anim)
        if hide:
            grp.finished.connect(self.hide)
        grp.start()
        self._anim_group = grp

    # ── Dragging ──────────────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag)
            e.accept()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            p = self.pos()
            self._pos_x, self._pos_y = p.x(), p.y()
            self._anchor_y = p.y()
            self._save_cfg()
            self._drag = None

    def _on_logs_click(self, e):
        log.debug("Opening live log viewer")
        if self._log_viewer is None:
            self._log_viewer = LogViewer(LOG_BUFFER, LOG_DIR)
        self._log_viewer.show()
        self._log_viewer.raise_()
        self._log_viewer.activateWindow()

    def _on_close_click(self, e):
        self._user_closed = True
        self.pop_out()

    def closeEvent(self, e):
        self.watcher.stop()
        if self._log_viewer is not None:
            self._log_viewer.close()
        super().closeEvent(e)


# ── Entry ───────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Segoe UI", 9))
    AdvisorWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
