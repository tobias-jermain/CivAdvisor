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

The overlay will auto-hide when the AIs move and re-appear when it's your turn.

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

## Accuracy Notes

- Victory percentages are **estimates** based on available Lua signals, not official game math.
- The advisor keeps its own weights and thresholds; actual game state may differ slightly.
- Board-aware rules (loyalty, districts, unit composition) only fire when the full-board exporter feeds them.

## Known Limitations

- Windows only (Lua.log path is hardcoded for Windows)
- Requires a mod exporting Lua state in the expected JSON format
- No in-game integration; runs as a separate overlay process
