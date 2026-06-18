"""
CivAdvisor — settings panel (slide-in)
======================================

A frameless, always-on-top panel that slides in from the right edge of the
screen. Two pages, navigated in place:

  Page 1 — Advisor mode (Engine / AI + Engine), Display, Data, System
  Page 2 — AI providers (Gemini / Groq / Ollama), keys, models, connection test

Edits write straight into the shared Config and persist on change; a
`changed` signal lets the overlay apply live preferences (opacity, mode).
"""

from __future__ import annotations

import logging

from PySide6.QtCore import (
    Qt, Signal, QPropertyAnimation, QEasingCurve, QPoint, QThread,
)
from PySide6.QtGui import QGuiApplication, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QRadioButton, QButtonGroup, QCheckBox, QSlider, QLineEdit, QComboBox,
    QStackedWidget, QFileDialog, QScrollArea,
)

import config as cfg_mod
import ai_advisor
from theme import (
    BG_TOP, BG_BOT, CARD_BG2, CARD_BG3, ACCENT, WARM, GOOD, TEXT_HI,
    TEXT_MID, TEXT_LO, BORDER, AI_ACCENT,
)

log = logging.getLogger("civadvisor")

PANEL_W = 372

PROVIDER_LABELS = {
    cfg_mod.PROVIDER_OPENROUTER: "OpenRouter (recommended — many free models)",
    cfg_mod.PROVIDER_GEMINI:     "Gemini (Google AI Studio)",
    cfg_mod.PROVIDER_GROQ:       "Groq (LLaMA 3, very fast)",
    cfg_mod.PROVIDER_OLLAMA:     "Ollama (local, no key)",
}
PROVIDER_HELP = {
    cfg_mod.PROVIDER_OPENROUTER: ("Free key, 50+ free models — openrouter.ai/keys",
                                  "https://openrouter.ai/keys"),
    cfg_mod.PROVIDER_GEMINI:     ("Free key, no credit card — aistudio.google.com/apikey",
                                  "https://aistudio.google.com/apikey"),
    cfg_mod.PROVIDER_GROQ:       ("Free key — console.groq.com/keys",
                                  "https://console.groq.com/keys"),
    cfg_mod.PROVIDER_OLLAMA:     ("Install & run Ollama locally — ollama.com",
                                  "https://ollama.com"),
}

_INPUT_CSS = (
    f"QLineEdit{{background:{BG_BOT};color:{TEXT_HI};border:1px solid {BORDER};"
    f"border-radius:7px;padding:6px 8px;font-size:11px;}}"
    f"QLineEdit:focus{{border:1px solid {ACCENT};}}"
)
_COMBO_CSS = (
    f"QComboBox{{background:{BG_BOT};color:{TEXT_HI};border:1px solid {BORDER};"
    f"border-radius:7px;padding:5px 8px;font-size:11px;}}"
    f"QComboBox QAbstractItemView{{background:{CARD_BG3};color:{TEXT_HI};"
    f"selection-background-color:{ACCENT};}}"
)
_BTN_CSS = (
    f"QPushButton{{color:{TEXT_HI};background:{CARD_BG3};border:1px solid {BORDER};"
    f"border-radius:7px;padding:6px 12px;font-size:11px;font-weight:600;}}"
    f"QPushButton:hover{{background:{CARD_BG2};}}"
)
_RADIO_CSS = f"QRadioButton{{color:{TEXT_HI};font-size:12px;spacing:7px;}}"
_CHECK_CSS = f"QCheckBox{{color:{TEXT_HI};font-size:11px;spacing:7px;}}"


def _section(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(f"color:{TEXT_LO};font-size:9px;font-weight:800;letter-spacing:1px;")
    return lab


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color:{TEXT_MID};font-size:10px;")
    return lab


class SettingsPanel(QWidget):
    changed = Signal()       # config mutated — overlay should re-apply live prefs

    def __init__(self, cfg: cfg_mod.Config):
        super().__init__()
        self.cfg = cfg
        self._test_worker: QThread | None = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(PANEL_W)
        self._visible = False
        self._anim = None

        screen = QGuiApplication.primaryScreen().geometry()
        self.scr_w, self.scr_h = screen.width(), screen.height()
        self.setFixedHeight(min(620, self.scr_h - 60))

        self._build()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("scard")
        card.setStyleSheet(f"""
            #scard {{
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                           stop:0 {BG_TOP}, stop:1 {BG_BOT});
                border-left: 1px solid {BORDER};
            }}
        """)
        root.addWidget(card)

        outer = QVBoxLayout(card)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        # Header
        header = QHBoxLayout()
        self.title = QLabel("⚙  Settings")
        self.title.setStyleSheet(f"color:{TEXT_HI};font-size:14px;font-weight:800;")
        close = QLabel("✕")
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet(f"color:{TEXT_LO};font-size:14px;")
        close.mousePressEvent = lambda e: self.slide_out()
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(close)
        outer.addLayout(header)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_main_page())
        self.stack.addWidget(self._build_ai_page())

    def _scroll_page(self) -> tuple:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{border:none;background:transparent;}"
            f"QScrollBar:vertical{{background:transparent;width:5px;margin:0;}}"
            f"QScrollBar::handle:vertical{{background:{BORDER};border-radius:2px;min-height:28px;}}"
            "QScrollBar::add-line,QScrollBar::sub-line{height:0;}")
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)
        scroll.setWidget(holder)
        return scroll, lay

    # ── Page 1: main ──────────────────────────────────────────────────────────

    def _build_main_page(self) -> QWidget:
        page, lay = self._scroll_page()

        # Advisor mode
        lay.addWidget(_section("ADVISOR MODE"))
        self.mode_group = QButtonGroup(self)
        self.rb_engine = QRadioButton("Engine Only")
        self.rb_ai = QRadioButton("AI + Engine")
        for rb in (self.rb_engine, self.rb_ai):
            rb.setStyleSheet(_RADIO_CSS)
            self.mode_group.addButton(rb)
            lay.addWidget(rb)
        self.rb_engine.setChecked(self.cfg.mode == cfg_mod.MODE_ENGINE)
        self.rb_ai.setChecked(self.cfg.mode == cfg_mod.MODE_AI)
        lay.addWidget(_hint("Engine Only is deterministic and offline — no key needed. "
                            "AI + Engine adds one LLM 'Commander's Note' on top each turn."))
        self.rb_engine.toggled.connect(self._on_mode_change)
        self.rb_ai.toggled.connect(self._on_mode_change)

        self.ai_config_btn = QPushButton("Configure AI providers  →")
        self.ai_config_btn.setStyleSheet(_BTN_CSS)
        self.ai_config_btn.setCursor(Qt.PointingHandCursor)
        self.ai_config_btn.clicked.connect(lambda: self.stack.setCurrentIndex(1))
        lay.addWidget(self.ai_config_btn)

        lay.addWidget(self._divider())

        # Display
        lay.addWidget(_section("DISPLAY"))
        op_row = QHBoxLayout()
        op_lab = QLabel("Opacity")
        op_lab.setStyleSheet(f"color:{TEXT_HI};font-size:11px;")
        self.op_val = QLabel(f"{int(self.cfg.opacity*100)}%")
        self.op_val.setStyleSheet(f"color:{ACCENT};font-size:11px;font-weight:700;")
        op_row.addWidget(op_lab)
        op_row.addStretch(1)
        op_row.addWidget(self.op_val)
        lay.addLayout(op_row)
        self.op_slider = QSlider(Qt.Horizontal)
        self.op_slider.setRange(50, 100)
        self.op_slider.setValue(int(self.cfg.opacity * 100))
        self.op_slider.setStyleSheet(
            f"QSlider::groove:horizontal{{height:4px;background:{CARD_BG3};border-radius:2px;}}"
            f"QSlider::handle:horizontal{{width:14px;margin:-6px 0;border-radius:7px;background:{ACCENT};}}"
            f"QSlider::sub-page:horizontal{{background:{ACCENT};border-radius:2px;}}")
        self.op_slider.valueChanged.connect(self._on_opacity)
        lay.addWidget(self.op_slider)

        self.cb_autoshow = QCheckBox("Auto-show overlay on turn change")
        self.cb_autoshow.setStyleSheet(_CHECK_CSS)
        self.cb_autoshow.setChecked(self.cfg.auto_show_on_turn)
        self.cb_autoshow.toggled.connect(self._on_autoshow)
        lay.addWidget(self.cb_autoshow)

        lay.addWidget(self._divider())

        # Data
        lay.addWidget(_section("DATA"))
        lay.addWidget(_hint("Civ VI Lua.log path. Leave blank to auto-detect."))
        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(self.cfg.log_path)
        self.path_edit.setStyleSheet(_INPUT_CSS)
        self.path_edit.setPlaceholderText("(auto-detect)")
        self.path_edit.editingFinished.connect(self._on_path_edit)
        browse = QPushButton("Browse")
        browse.setStyleSheet(_BTN_CSS)
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self._on_browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        lay.addLayout(path_row)

        lay.addWidget(self._divider())

        # System
        lay.addWidget(_section("SYSTEM"))
        self.cb_tray = QCheckBox("Minimize to tray on close (keep running)")
        self.cb_tray.setStyleSheet(_CHECK_CSS)
        self.cb_tray.setChecked(self.cfg.minimize_to_tray)
        self.cb_tray.toggled.connect(self._on_tray_toggle)
        lay.addWidget(self.cb_tray)

        lay.addWidget(self._divider())

        # Auto-execute
        lay.addWidget(_section("AUTO-EXECUTE (EXPERIMENTAL)"))
        self.cb_auto = QCheckBox("Auto-queue research & civics when nothing is set")
        self.cb_auto.setStyleSheet(_CHECK_CSS)
        self.cb_auto.setChecked(self.cfg.auto_execute)
        self.cb_auto.toggled.connect(self._on_auto_toggle)
        lay.addWidget(self.cb_auto)

        self.cb_auto_prod = QCheckBox("Auto-fill empty city production queues")
        self.cb_auto_prod.setStyleSheet(_CHECK_CSS)
        self.cb_auto_prod.setChecked(self.cfg.auto_production)
        self.cb_auto_prod.toggled.connect(self._on_auto_prod_toggle)
        lay.addWidget(self.cb_auto_prod)

        self.cb_auto_pol = QCheckBox("Auto-fill empty policy card slots")
        self.cb_auto_pol.setStyleSheet(_CHECK_CSS)
        self.cb_auto_pol.setChecked(self.cfg.auto_policies)
        self.cb_auto_pol.toggled.connect(self._on_auto_pol_toggle)
        lay.addWidget(self.cb_auto_pol)

        self.cb_auto_units = QCheckBox("Auto-control units (tactics + combat)")
        self.cb_auto_units.setStyleSheet(_CHECK_CSS)
        self.cb_auto_units.setChecked(self.cfg.auto_units)
        self.cb_auto_units.toggled.connect(self._on_auto_units_toggle)
        lay.addWidget(self.cb_auto_units)

        lay.addWidget(_hint(
            "Requires the AutoAdvisor Lua mod active in-game. Production and "
            "policies only fill empty slots; units that you've already moved "
            "this turn are left alone. Unit control will attack — turn it off "
            "if you want to fight manually."
        ))

        lay.addStretch(1)
        return page

    # ── Page 2: AI providers ──────────────────────────────────────────────────

    def _build_ai_page(self) -> QWidget:
        page, lay = self._scroll_page()

        back = QPushButton("←  Back")
        back.setStyleSheet(_BTN_CSS)
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        lay.addWidget(back)

        lay.addWidget(_section("PROVIDER"))
        self.provider_group = QButtonGroup(self)
        self._provider_radios = {}
        for prov in cfg_mod.VALID_PROVIDERS:
            rb = QRadioButton(PROVIDER_LABELS[prov])
            rb.setStyleSheet(_RADIO_CSS)
            rb.setChecked(self.cfg.provider == prov)
            rb.toggled.connect(lambda checked, p=prov: self._on_provider_change(p, checked))
            self.provider_group.addButton(rb)
            self._provider_radios[prov] = rb
            lay.addWidget(rb)

        lay.addWidget(self._divider())

        # Per-provider fields (rebuilt to reflect the selected provider)
        self.prov_help = _hint("")
        self.prov_help.setOpenExternalLinks(False)
        self.prov_help.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        lay.addWidget(self.prov_help)

        self.key_label = QLabel("API key")
        self.key_label.setStyleSheet(f"color:{TEXT_HI};font-size:11px;")
        lay.addWidget(self.key_label)
        self.key_edit = QLineEdit()
        self.key_edit.setStyleSheet(_INPUT_CSS)
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("Paste your key")
        self.key_edit.editingFinished.connect(self._on_key_edit)
        lay.addWidget(self.key_edit)

        endp_label = QLabel("Ollama endpoint")
        endp_label.setStyleSheet(f"color:{TEXT_HI};font-size:11px;")
        self.endp_label = endp_label
        lay.addWidget(endp_label)
        self.endp_edit = QLineEdit(self.cfg.ollama_endpoint)
        self.endp_edit.setStyleSheet(_INPUT_CSS)
        self.endp_edit.setPlaceholderText("http://localhost:11434")
        self.endp_edit.editingFinished.connect(self._on_endpoint_edit)
        lay.addWidget(self.endp_edit)

        model_label = QLabel("Model")
        model_label.setStyleSheet(f"color:{TEXT_HI};font-size:11px;")
        lay.addWidget(model_label)
        self.model_edit = QLineEdit()
        self.model_edit.setStyleSheet(_INPUT_CSS)
        self.model_edit.editingFinished.connect(self._on_model_edit)
        lay.addWidget(self.model_edit)

        test_row = QHBoxLayout()
        self.test_btn = QPushButton("Test connection")
        self.test_btn.setStyleSheet(_BTN_CSS)
        self.test_btn.setCursor(Qt.PointingHandCursor)
        self.test_btn.clicked.connect(self._on_test)
        test_row.addWidget(self.test_btn)
        test_row.addStretch(1)
        lay.addLayout(test_row)

        self.test_status = QLabel("")
        self.test_status.setWordWrap(True)
        self.test_status.setStyleSheet(f"color:{TEXT_MID};font-size:10px;")
        lay.addWidget(self.test_status)

        lay.addStretch(1)
        self._sync_provider_fields()
        return page

    def _divider(self) -> QFrame:
        d = QFrame()
        d.setFixedHeight(1)
        d.setStyleSheet(f"background:{BORDER};")
        return d

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _on_mode_change(self, _checked=False):
        self.cfg.mode = cfg_mod.MODE_AI if self.rb_ai.isChecked() else cfg_mod.MODE_ENGINE
        self.ai_config_btn.setEnabled(True)
        self.cfg.save()
        self.changed.emit()

    def _on_opacity(self, val: int):
        self.op_val.setText(f"{val}%")
        self.cfg.opacity = val / 100.0
        self.cfg.save()
        self.changed.emit()

    def _on_autoshow(self, checked: bool):
        self.cfg.auto_show_on_turn = checked
        self.cfg.save()

    def _on_tray_toggle(self, checked: bool):
        self.cfg.minimize_to_tray = checked
        self.cfg.save()

    def _on_auto_toggle(self, checked: bool):
        self.cfg.auto_execute = checked
        self.cfg.save()
        self.changed.emit()

    def _on_auto_prod_toggle(self, checked: bool):
        self.cfg.auto_production = checked
        self.cfg.save()
        self.changed.emit()

    def _on_auto_pol_toggle(self, checked: bool):
        self.cfg.auto_policies = checked
        self.cfg.save()
        self.changed.emit()

    def _on_auto_units_toggle(self, checked: bool):
        self.cfg.auto_units = checked
        self.cfg.save()
        self.changed.emit()

    def _on_path_edit(self):
        self.cfg.log_path = self.path_edit.text().strip()
        self.cfg.save()

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Lua.log", "",
                                              "Log files (*.log);;All files (*)")
        if path:
            self.path_edit.setText(path)
            self.cfg.log_path = path
            self.cfg.save()

    def _on_provider_change(self, prov: str, checked: bool):
        if not checked:
            return
        self.cfg.provider = prov
        self.cfg.save()
        self._sync_provider_fields()
        self.changed.emit()

    def _sync_provider_fields(self):
        prov = self.cfg.provider
        is_ollama = prov == cfg_mod.PROVIDER_OLLAMA
        text, url = PROVIDER_HELP[prov]
        self.prov_help.setText(f'{text}<br><a href="{url}" style="color:{ACCENT};">Open link</a>')
        # Key field hidden for Ollama; endpoint shown only for Ollama.
        self.key_label.setVisible(not is_ollama)
        self.key_edit.setVisible(not is_ollama)
        if not is_ollama:
            self.key_edit.setText(self.cfg.api_keys.get(prov, ""))
        self.endp_label.setVisible(is_ollama)
        self.endp_edit.setVisible(is_ollama)
        self.model_edit.setText(self.cfg.current_model())
        self.test_status.setText("")

    def _on_key_edit(self):
        self.cfg.api_keys[self.cfg.provider] = self.key_edit.text().strip()
        self.cfg.save()

    def _on_endpoint_edit(self):
        self.cfg.ollama_endpoint = self.endp_edit.text().strip() or \
            cfg_mod.PROVIDER_DEFAULTS[cfg_mod.PROVIDER_OLLAMA]["endpoint"]
        self.cfg.save()

    def _on_model_edit(self):
        self.cfg.ai_models[self.cfg.provider] = self.model_edit.text().strip() or \
            cfg_mod.PROVIDER_DEFAULTS[self.cfg.provider]["model"]
        self.cfg.save()

    def _on_test(self):
        if self._test_worker is not None and self._test_worker.isRunning():
            return
        # Flush whatever is currently typed into the key/endpoint/model fields
        # before reading back from cfg — editingFinished may not have fired yet.
        self._on_key_edit()
        self._on_endpoint_edit()
        self._on_model_edit()
        prov = self.cfg.provider
        self.test_status.setStyleSheet(f"color:{TEXT_MID};font-size:10px;")
        self.test_status.setText("Testing…")
        self.test_btn.setEnabled(False)

        worker = _TestWorker(prov, self.cfg.current_key(),
                             self.cfg.current_model(), self.cfg.ollama_endpoint)
        worker.result.connect(self._on_test_result)
        worker.finished.connect(lambda: self.test_btn.setEnabled(True))
        self._test_worker = worker
        worker.start()

    def _on_test_result(self, ok: bool, msg: str):
        colour = GOOD if ok else WARM
        self.test_status.setStyleSheet(f"color:{colour};font-size:10px;")
        self.test_status.setText(("✓ " if ok else "✕ ") + msg)

    # ── Slide animation ─────────────────────────────────────────────────────────

    def _target_pos(self) -> QPoint:
        y = max(20, (self.scr_h - self.height()) // 2)
        return QPoint(self.scr_w - PANEL_W, y)

    def slide_in(self):
        if self._visible:
            self.raise_()
            self.activateWindow()
            return
        self._visible = True
        self.stack.setCurrentIndex(0)
        tgt = self._target_pos()
        off = QPoint(self.scr_w, tgt.y())
        self.move(off)
        self.show()
        self.raise_()
        self._animate(off, tgt)

    def slide_out(self):
        if not self._visible:
            return
        self._visible = False
        cur = self.pos()
        self._animate(cur, QPoint(self.scr_w, cur.y()), hide=True)

    def _animate(self, p0: QPoint, p1: QPoint, hide=False):
        anim = QPropertyAnimation(self, b"pos")
        anim.setDuration(260)
        anim.setStartValue(p0)
        anim.setEndValue(p1)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        if hide:
            anim.finished.connect(self.hide)
        anim.start()
        self._anim = anim

    def toggle(self):
        self.slide_out() if self._visible else self.slide_in()


class _TestWorker(QThread):
    result = Signal(bool, str)

    def __init__(self, provider, api_key, model, endpoint):
        super().__init__()
        self._a = (provider, api_key, model, endpoint)

    def run(self):
        prov, key, model, endpoint = self._a
        ok, msg = ai_advisor.test_connection(prov, api_key=key, model=model,
                                             endpoint=endpoint)
        self.result.emit(ok, msg)
