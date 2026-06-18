"""
CivAdvisor — configuration
===========================

A single typed config object, loaded from / saved to civadvisor_ui.json. Holds
window position, victory focus, advisor mode, AI provider settings and display
preferences. Migrates the old v1 file (x / y / focus only) on first load.

No Qt, no engine — pure state so it can be imported anywhere.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("civadvisor")

CONFIG_VERSION = 2

# Advisor modes
MODE_ENGINE = "engine"          # deterministic engine only (default, no key needed)
MODE_AI     = "ai_engine"       # engine + an AI "Commander's Note"
VALID_MODES = (MODE_ENGINE, MODE_AI)

# AI providers (all free-tier capable)
PROVIDER_GEMINI      = "gemini"
PROVIDER_GROQ        = "groq"
PROVIDER_OLLAMA      = "ollama"
PROVIDER_OPENROUTER  = "openrouter"
VALID_PROVIDERS = (PROVIDER_GEMINI, PROVIDER_GROQ, PROVIDER_OLLAMA, PROVIDER_OPENROUTER)

VALID_FOCUS = ("auto", "science", "culture", "domination", "religion", "diplomacy")

# Sensible per-provider defaults (free models, no card required where noted).
PROVIDER_DEFAULTS = {
    PROVIDER_GEMINI:     {"model": "gemini-2.0-flash",                    "endpoint": ""},
    PROVIDER_GROQ:       {"model": "llama-3.3-70b-versatile",             "endpoint": ""},
    PROVIDER_OLLAMA:     {"model": "llama3.1",                            "endpoint": "http://localhost:11434"},
    PROVIDER_OPENROUTER: {"model": "meta-llama/llama-3.3-70b-instruct:free", "endpoint": ""},
}


class Config:
    """Typed settings with JSON persistence and v1→v2 migration."""

    __slots__ = (
        "_path",
        "x", "y", "focus",
        "mode", "provider", "api_keys", "ai_models", "ollama_endpoint",
        "opacity", "auto_show_on_turn", "minimize_to_tray",
        "log_path", "auto_execute",
        "auto_production", "auto_policies", "auto_units",
    )

    def __init__(self, path: str):
        self._path = path
        # Window / focus
        self.x = None
        self.y = None
        self.focus = "auto"
        # Advisor
        self.mode = MODE_ENGINE
        self.provider = PROVIDER_OPENROUTER
        self.api_keys = {p: "" for p in VALID_PROVIDERS}    # per-provider keys
        self.ai_models = {p: PROVIDER_DEFAULTS[p]["model"] for p in VALID_PROVIDERS}
        self.ollama_endpoint = PROVIDER_DEFAULTS[PROVIDER_OLLAMA]["endpoint"]
        # Display
        self.opacity = 1.0
        self.auto_show_on_turn = True
        self.minimize_to_tray = True
        # Data
        self.log_path = ""
        # Automation
        self.auto_execute = False        # master: research + civics
        self.auto_production = False     # fill empty city build queues
        self.auto_policies = False       # fill empty policy slots
        self.auto_units = False          # full unit tactics, incl. combat

    # ── Load / migrate ────────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: str) -> "Config":
        cfg = cls(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return cfg            # fresh defaults
        if not isinstance(raw, dict):
            return cfg

        version = int(raw.get("version", 1))
        if version < 2:
            raw = cls._migrate_v1(raw)

        cfg.x = raw.get("x")
        cfg.y = raw.get("y")
        cfg.focus = raw.get("focus", "auto")
        if cfg.focus not in VALID_FOCUS:
            cfg.focus = "auto"

        cfg.mode = raw.get("mode", MODE_ENGINE)
        if cfg.mode not in VALID_MODES:
            cfg.mode = MODE_ENGINE
        cfg.provider = raw.get("provider", PROVIDER_OPENROUTER)
        if cfg.provider not in VALID_PROVIDERS:
            cfg.provider = PROVIDER_OPENROUTER

        keys = raw.get("api_keys", {})
        if isinstance(keys, dict):
            for p in VALID_PROVIDERS:
                cfg.api_keys[p] = str(keys.get(p, ""))
        models = raw.get("ai_models", {})
        if isinstance(models, dict):
            for p in VALID_PROVIDERS:
                cfg.ai_models[p] = str(models.get(p) or PROVIDER_DEFAULTS[p]["model"])
        cfg.ollama_endpoint = raw.get("ollama_endpoint") or PROVIDER_DEFAULTS[PROVIDER_OLLAMA]["endpoint"]

        cfg.opacity = _clampf(raw.get("opacity", 1.0), 0.5, 1.0)
        cfg.auto_show_on_turn = bool(raw.get("auto_show_on_turn", True))
        cfg.minimize_to_tray = bool(raw.get("minimize_to_tray", True))
        cfg.log_path = str(raw.get("log_path", raw.get("logPath", "")) or "")
        cfg.auto_execute = bool(raw.get("auto_execute", False))
        cfg.auto_production = bool(raw.get("auto_production", False))
        cfg.auto_policies = bool(raw.get("auto_policies", False))
        cfg.auto_units = bool(raw.get("auto_units", False))
        return cfg

    @staticmethod
    def _migrate_v1(raw: dict) -> dict:
        """v1 held only x / y / focus / logPath — keep them, add v2 defaults."""
        log.info("Migrating config v1 → v2")
        out = dict(raw)
        out["version"] = CONFIG_VERSION
        if "logPath" in raw and "log_path" not in raw:
            out["log_path"] = raw["logPath"]
        return out

    # ── Save ────────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "version": CONFIG_VERSION,
            "x": self.x, "y": self.y, "focus": self.focus,
            "mode": self.mode, "provider": self.provider,
            "api_keys": dict(self.api_keys), "ai_models": dict(self.ai_models),
            "ollama_endpoint": self.ollama_endpoint,
            "opacity": self.opacity,
            "auto_show_on_turn": self.auto_show_on_turn,
            "minimize_to_tray": self.minimize_to_tray,
            "log_path": self.log_path,
            "auto_execute": self.auto_execute,
            "auto_production": self.auto_production,
            "auto_policies": self.auto_policies,
            "auto_units": self.auto_units,
        }

    def save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2)
        except Exception:
            log.error("Failed to save config", exc_info=True)

    # ── Convenience ───────────────────────────────────────────────────────────

    @property
    def ai_enabled(self) -> bool:
        return self.mode == MODE_AI

    def current_key(self) -> str:
        return self.api_keys.get(self.provider, "")

    def current_model(self) -> str:
        return self.ai_models.get(self.provider) or PROVIDER_DEFAULTS[self.provider]["model"]


def _clampf(v, lo, hi) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return hi
