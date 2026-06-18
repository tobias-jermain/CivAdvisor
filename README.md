# CivAdvisor

A real-time, always-on-top overlay advisor for **Sid Meier's Civilization VI**. CivAdvisor monitors your game state live and delivers context-aware strategic recommendations across economy, military, victory paths, and city management.

## Features

- **Live Game Monitoring**: Reads Civilization VI's Lua logs in real-time. No network, no rate limits.
- **Victory Path Tracking**: Estimates progress across all five victory conditions:
  - Science (via tech completion & research ranking)
  - Culture (via tourism metrics)
  - Domination (via captured enemy capitals)
  - Religion (via religious city spread)
  - Diplomacy (via victory points)
- **Smart Tip Engine**: Weighted, prioritized advice covering:
  - Economy & gold flow
  - Science & culture production
  - City growth, housing, and amenities
  - Military composition & war strategy
  - Threat detection (enemy units near your cities)
  - Civ-specific strategies (all 40+ leaders profiled)
  - Era-specific playbook (Ancient through Future)
- **Draggable Pop-up UI**: Compact, Wispr-styled overlay with smooth animations
- **Tab-based Organization**: "Now" (urgent), "Plan" (long-term), "Cities" (local management)
- **Victory Focus Control**: Lock onto a specific path or let the engine auto-select
- **Board-Aware Rules**: Deep game state analysis including districts, loyalty, trade routes, unit composition, and strategic resources (when full-board exporter is enabled)

## How It Works

1. CivAdvisor watches the game's `Lua.log` file for state snapshots your mod exports
2. The offline advisor engine analyzes your position, rivals, and goals
3. Recommendations appear in a pop-up ranked by urgency
4. Drag the window freely; position is remembered between sessions
5. Switch tabs to see urgent problems, strategic plans, or city-specific actions

## Requirements

- **Python 3.8+**
- **PySide6** (Qt 6 bindings): `pip install PySide6`
- **Windows 10+** (path hardcoded for `%LOCALAPPDATA%`)
- **Civilization VI** with Lua logging enabled

## Installation

1. Clone or extract this repo to your machine
2. Install dependencies: `pip install PySide6`
3. Ensure Civ VI logs are enabled (Lua.log typically at `%LOCALAPPDATA%\Firaxis Games\Sid Meier's Civilization VI\Logs\`)
4. Run `python overlay/main.py`

The overlay stays open while you play. When you end your turn it keeps the
window up and shows a loading spinner on each stat while the AIs move, then
refreshes with the new turn's numbers. Logs are written to
`%LOCALAPPDATA%\CivAdvisor\logs` (frozen build) or next to the source (dev).

## Architecture

```
overlay/
├── main.py                # PySide6 UI: window, animations, drag handling
├── advisor_engine.py      # Core logic: state analysis, rule engine, tip generation
└── board.py              # State assembly: transforms raw Lua snapshots into game context
```

- **LogWatcher** (main.py): Threaded file monitor that parses incoming Lua log entries
- **AdvisorWindow** (main.py): Qt widget managing the overlay UI, animations, and config persistence
- **compute_report()** (advisor_engine.py): Main entry point; applies 30+ rules to produce ranked tips
- **CIV_PROFILES**: Hardcoded leader/civ strategy profiles (focus vector, unique tips)
- **PHASE_PLAYBOOK**: Era-based guidance (when to expand, attack, build wonders, etc.)

## Game State Input

The mod exports game state to Lua.log as JSON snapshots. Core fields:

- `turn`, `era`, `eraIndex`, `civ`, `leader`
- `score`, `gold`, `gpt`, `science`, `culture`, `faith`, `tourism`
- `currentTech`, `techTurns`, `civics done`, etc.
- `cityData[]`: per-city details (name, production, food, amenities, pop, housing)
- `rivals[]`: opponent stats (military, science, culture, score)
- `threats[]`: enemy units near your cities
- `unitSummary`: unit counts by class (military, siege, cavalry, etc.)
- `strategics`: resource counts (HORSES, IRON, NITER, OIL, etc.)

See `advisor_engine._context()` for the full list of derived fields.

## UI Customization

Position and victory focus are saved to `civadvisor_ui.json` in the working directory:

```json
{"x": 1234, "y": 56, "focus": "science"}
```

Edit or delete to reset. Design tokens (colors, spacing, fonts) are hardcoded in `main.py:BG_TOP`, `ACCENT`, etc.

## Multiplayer (FireTuner, no mod required)

Civ VI multiplayer requires every player in the lobby to have the same enabled
mods, so the installed mod can't be used unless all players install it. To get
the same data in multiplayer **without anyone else installing anything**, use
the FireTuner exporter — it runs only on your local client:

1. Install **Sid Meier's Civilization VI Development Tools** (free, on Steam).
2. In `AppOptions.txt` set `EnableTuner 1`.
3. Launch the game, enter your match, then open **FireTuner** and connect.
4. Load `firetuner/CivAdvisor_Tuner.lua` against the **InGame** context and run it.

It prints the same `CIV_ADVISOR_...` lines to `Lua.log`, so the overlay works
unchanged. It's read-only and print-only — multiplayer-safe, no desync, no
gameplay effect. Call `CivAdvisorDump()` in the console to force a refresh.

> Note: Civ VI **disables the Lua tuner in multiplayer**, so FireTuner only
> works in single-player. For multiplayer, publish the mod to the Steam
> Workshop (below) so every player can subscribe with one click.

## Publishing to the Steam Workshop

Multiplayer requires every player to have the host's enabled mods. The
friction-free way to distribute is the Steam Workshop — players just click
**Subscribe** (no file downloads), and Civ VI auto-syncs subscribed mods in
multiplayer lobbies. Publish with ModBuddy (installed with the Dev Tools):

1. Make sure the mod folder is in your Mods directory:
   `Documents\My Games\Sid Meier's Civilization VI\Mods\CivAdvisor`
   (the `CivAdvisor.modinfo` + `UI\` files from `lua_mod/`).
2. Open **ModBuddy** (from the Civ VI Development Tools).
3. **File → Import → Import existing mod**, and pick the `CivAdvisor` folder.
   ModBuddy reads the metadata from `CivAdvisor.modinfo`.
4. **Build** the project (Build → Build Solution).
5. With **Steam running**, right-click the project → **Publish to Steam
   Workshop** (first publish creates the Workshop item; later publishes update
   it). Set visibility to Public (or Friends-only) and accept the agreement.
6. Send friends the Workshop link → they click **Subscribe**, enable it once in
   **Additional Content**, and can then join your modded multiplayer lobby.

Only the host needs the overlay and `EnableTuner`; friends only need to be
subscribed so the lobby's mod check passes.

## Accuracy Notes

- Victory percentages are **estimates** based on available Lua signals, not official game math.
- The advisor keeps its own weights and thresholds; actual game state may differ slightly.
- Board-aware rules (loyalty, districts, unit composition) only fire when the full-board exporter feeds them.

## Known Limitations

- Windows only (Lua.log path is hardcoded for Windows)
- Requires a mod exporting Lua state in the expected JSON format
- No in-game integration; runs as a separate overlay process
