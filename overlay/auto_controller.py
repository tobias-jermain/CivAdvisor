"""
CivAdvisor — auto-execute controller
=====================================

Derives typed commands from the current game state and writes them to
%TEMP%/civadvisor_commands.json for the AutoAdvisor Lua mod to pick up on
the next turn start. Results come back as CIV_ADVISOR_CMD_RESULT lines in
Lua.log, which the existing log watcher already reads.

Only research and civic queuing are automated in this first version — the
two highest-value, lowest-risk automated actions. Production and unit moves
are left for manual play.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("civadvisor")

# Shared temp-file location — must match the path in AutoAdvisor.lua
_TEMP = os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp"
COMMANDS_FILE = os.path.join(_TEMP, "civadvisor_commands.json")
RESULTS_PREFIX = "CIV_ADVISOR_CMD_RESULT"


# ── Command derivation ────────────────────────────────────────────────────────

def derive_commands(state: dict, focus: str) -> list[dict]:
    """Return the list of auto-execute commands for this turn.

    Commands are only emitted when the game has nothing queued (research /
    civic shows 'none') so we never overwrite a deliberate player choice.
    """
    cmds: list[dict] = []
    turn = int(state.get("turn", 0))

    cur_tech = (state.get("currentTech") or "none").strip().lower()
    if cur_tech in ("none", "unknown", ""):
        cmds.append({
            "id": f"r_{turn}",
            "type": "auto_research",
            "focus": focus or "auto",
        })

    cur_civic = (state.get("currentCivic") or "none").strip().lower()
    if cur_civic in ("none", "unknown", ""):
        cmds.append({
            "id": f"c_{turn}",
            "type": "auto_civic",
            "focus": focus or "auto",
        })

    return cmds


def write_commands(turn: int, commands: list[dict]) -> bool:
    """Write the commands JSON file. Returns True on success."""
    if not commands:
        return True
    payload = {"version": 1, "turn": turn, "commands": commands}
    try:
        with open(COMMANDS_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        log.debug("Wrote %d auto-command(s) for turn %d", len(commands), turn)
        return True
    except Exception:
        log.warning("Failed to write auto-commands file", exc_info=True)
        return False


def clear_commands() -> None:
    """Remove the commands file so Lua doesn't re-execute stale commands."""
    try:
        if os.path.exists(COMMANDS_FILE):
            os.remove(COMMANDS_FILE)
    except Exception:
        pass


# ── Result parsing ────────────────────────────────────────────────────────────

def parse_result_line(line: str) -> dict | None:
    """Parse a CIV_ADVISOR_CMD_RESULT line. Returns dict or None."""
    if RESULTS_PREFIX not in line:
        return None
    try:
        idx = line.index(RESULTS_PREFIX) + len(RESULTS_PREFIX)
        return json.loads(line[idx:].strip())
    except Exception:
        return None


def result_label(result: dict) -> str:
    """Short human-readable label for a command result (shown in the overlay)."""
    ok  = result.get("ok", False)
    typ = result.get("type", "")
    val = result.get("value", "")
    if typ == "auto_research":
        return f"{'✓' if ok else '✕'} Research → {val or ('queued' if ok else 'failed')}"
    if typ == "auto_civic":
        return f"{'✓' if ok else '✕'} Civic → {val or ('queued' if ok else 'failed')}"
    return f"{'✓' if ok else '✕'} {typ}"
