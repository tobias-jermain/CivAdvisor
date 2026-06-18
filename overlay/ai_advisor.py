"""
CivAdvisor — AI advisor (optional "Commander's Note")
=====================================================

When the user enables AI + Engine mode, the deterministic engine runs first as
always; this module then asks a free-tier LLM for ONE concrete strategic
insight tuned to the exact game state, shown as a distinct card at the top.

Three providers, all usable on a free tier, no extra heavyweight SDKs — every
call is a plain HTTPS request via urllib so the frozen build stays small:

  • Gemini  — Google AI Studio key (no credit card), generous free quota
  • Groq    — groq.com key, very fast LLaMA 3 inference
  • Ollama  — fully local, no key, needs the Ollama app running

The network call runs on a QThread (AiWorker) so the UI never blocks. Failures
are swallowed into a short message; the engine output always stands alone.
"""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error

from PySide6.QtCore import QThread, Signal

import config as cfg_mod

log = logging.getLogger("civadvisor")

REQUEST_TIMEOUT = 8          # seconds — keep the note snappy or skip it
MAX_TIPS_IN_PROMPT = 3

SYSTEM_PREAMBLE = (
    "You are an expert Civilization VI strategy advisor giving terse, concrete, "
    "high-level advice. No preamble, no hedging, no markdown headers."
)


# ── Prompt construction ───────────────────────────────────────────────────────

def build_prompt(state: dict, report: dict) -> str:
    meta = report.get("meta", {})
    vics = report.get("victories", [])
    strongest = report.get("strongest", "")
    rank_txt = ""
    for v in vics:
        if v.get("key") == strongest and v.get("total"):
            rank_txt = f" (rank {v['rank']}/{v['total']})"
            break

    def _f(v):
        try:
            return f"{float(v):.0f}"
        except (TypeError, ValueError):
            return "?"

    tips = report.get("tips", [])[:MAX_TIPS_IN_PROMPT]
    tip_lines = []
    for i, t in enumerate(tips, 1):
        body = (t.get("body", "") or "")[:140]
        tip_lines.append(f"  {i}. {t.get('title','')} — {body}")
    tips_block = "\n".join(tip_lines) if tip_lines else "  (none)"

    return (
        f"{SYSTEM_PREAMBLE}\n\n"
        f"Civilization VI — current situation:\n"
        f"Turn {meta.get('turn','?')}, Era: {meta.get('era','?')}, "
        f"Leader/Civ: {meta.get('leader','?')} / {meta.get('civ','?')}\n"
        f"Yields per turn — Gold: {_f(state.get('gpt'))}, "
        f"Science: {_f(state.get('science'))}, Culture: {_f(state.get('culture'))}, "
        f"Faith: {_f(state.get('faith'))}\n"
        f"Best victory path: {strongest or 'undecided'}{rank_txt}\n"
        f"Engine's top recommendations:\n{tips_block}\n\n"
        f"In 2-3 sentences, give ONE specific, actionable strategic insight for "
        f"THIS exact turn that the engine tips above do not already cover. "
        f"Be concrete (name units, districts, policies, or wonders). No preamble."
    )


# ── Provider calls (plain HTTPS) ──────────────────────────────────────────────

def _http_post(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def query_gemini(prompt: str, api_key: str, model: str) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={api_key}")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 200},
    }
    data = _http_post(url, payload, {"Content-Type": "application/json"})
    cands = data.get("candidates", [])
    if not cands:
        raise RuntimeError("Gemini returned no candidates")
    parts = cands[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip()


def query_groq(prompt: str, api_key: str, model: str) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7, "max_tokens": 200,
    }
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_key}"}
    data = _http_post(url, payload, headers)
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("Groq returned no choices")
    return choices[0].get("message", {}).get("content", "").strip()


def query_ollama(prompt: str, endpoint: str, model: str) -> str:
    base = (endpoint or "http://localhost:11434").rstrip("/")
    url = f"{base}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False,
               "options": {"temperature": 0.7, "num_predict": 200}}
    data = _http_post(url, payload, {"Content-Type": "application/json"})
    return (data.get("response", "") or "").strip()


def query(provider: str, prompt: str, *, api_key: str = "", model: str = "",
          endpoint: str = "") -> str:
    if provider == cfg_mod.PROVIDER_GEMINI:
        if not api_key:
            raise RuntimeError("No Gemini API key set")
        return query_gemini(prompt, api_key, model or "gemini-2.0-flash")
    if provider == cfg_mod.PROVIDER_GROQ:
        if not api_key:
            raise RuntimeError("No Groq API key set")
        return query_groq(prompt, api_key, model or "llama-3.3-70b-versatile")
    if provider == cfg_mod.PROVIDER_OLLAMA:
        return query_ollama(prompt, endpoint, model or "llama3.1")
    raise RuntimeError(f"Unknown provider: {provider}")


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return "AI key rejected — check it in Settings."
        if exc.code == 429:
            return "AI rate limit hit — try again shortly."
        return f"AI request failed (HTTP {exc.code})."
    if isinstance(exc, urllib.error.URLError):
        return "AI unreachable — check your connection (or that Ollama is running)."
    if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
        return "AI timed out — engine tips shown only."
    return "AI note unavailable this turn."


# ── Worker thread ─────────────────────────────────────────────────────────────

class AiWorker(QThread):
    """One-shot worker: emits done(text) on success or failed(message)."""

    done   = Signal(str)
    failed = Signal(str)

    def __init__(self, provider, prompt, *, api_key="", model="", endpoint=""):
        super().__init__()
        self._provider = provider
        self._prompt   = prompt
        self._api_key  = api_key
        self._model    = model
        self._endpoint = endpoint

    def run(self):
        try:
            text = query(self._provider, self._prompt,
                          api_key=self._api_key, model=self._model,
                          endpoint=self._endpoint)
            if text:
                self.done.emit(text)
            else:
                self.failed.emit("AI returned an empty response.")
        except Exception as e:           # noqa: BLE001 — surface as a friendly note
            log.warning(f"AI query failed: {e}")
            self.failed.emit(friendly_error(e))


def test_connection(provider, *, api_key="", model="", endpoint="") -> tuple:
    """Synchronous probe used by the Settings 'Test' button. Returns (ok, msg)."""
    try:
        text = query(provider, "Reply with the single word: OK",
                     api_key=api_key, model=model, endpoint=endpoint)
        if text:
            return True, "Connection OK."
        return False, "Empty response from provider."
    except Exception as e:               # noqa: BLE001
        return False, friendly_error(e)
