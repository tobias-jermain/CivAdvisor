"""
CivAdvisor — board model
========================

Turns a reassembled full-board snapshot (player + cities + units + rivals +
city-states) into one `state` dict the engine consumes. It keeps every legacy
flat field so existing rules keep working, and adds rich fields + derived
analytics (unit composition, threats) that the new smart rules use.

Pure data, no game/network calls.
"""

from __future__ import annotations


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ── Unit classification ──────────────────────────────────────────────────────
# Match on the (UNIT_-stripped) type string with keyword sets, falling back to
# the combat/ranged/bombard numbers. Robust to civ-unique unit names.

_SIEGE   = ("CATAPULT", "BOMBARD", "ARTILLERY", "TREBUCHET", "ROCKET")
_RANGED  = ("SLINGER", "ARCHER", "CROSSBOW", "FIELD_CANNON", "MACHINE_GUN", "CANNON")
_CAVALRY = ("HORSEMAN", "CHARIOT", "KNIGHT", "COURSER", "CAVALRY", "CUIRASSIER",
            "TANK", "HELICOPTER", "ARMOR", "WARCART", "WAR_CART", "HORSE", "SAKA",
            "BARBARIAN_HORSE", "MAMLUK", "WINGED_HUSSAR", "BLACK_ARMY")
_NAVAL   = ("GALLEY", "QUADRIREME", "BIREME", "CARAVEL", "FRIGATE", "IRONCLAD",
            "DESTROYER", "BATTLESHIP", "SUBMARINE", "CRUISER", "CARRIER",
            "PRIVATEER", "GALLEAS", "LONGSHIP", "DROMON", "MINAS", "DE_RUYTER")
_RECON   = ("SCOUT", "RANGER", "SKIRMISHER")
_AIR     = ("BIPLANE", "FIGHTER", "BOMBER", "JET")
_SUPPORT = ("BATTERING_RAM", "SIEGE_TOWER", "MILITARY_ENGINEER", "MEDIC",
            "SUPPLY_CONVOY", "OBSERVATION_BALLOON", "ANTI_AIR", "MOBILE_SAM", "DRONE")
_CIVILIAN = ("SETTLER", "BUILDER", "TRADER", "MISSIONARY", "APOSTLE", "INQUISITOR",
             "GURU", "ARCHAEOLOGIST", "NATURALIST", "ROCK_BAND", "SPY", "GREAT_",
             "WARRIOR_MONK")


def _has(name, keys):
    return any(k in name for k in keys)


def classify_unit(u: dict) -> str:
    name = str(u.get("type", "")).upper()
    combat  = _num(u.get("combat"))
    ranged  = _num(u.get("ranged"))
    bombard = _num(u.get("bombard"))

    if combat == 0 and ranged == 0 and bombard == 0:
        return "support" if _has(name, _SUPPORT) else "civilian"
    if _has(name, _SUPPORT):
        return "support"
    if _has(name, _SIEGE) or (bombard > 0 and not _has(name, _NAVAL)):
        return "siege"
    if _has(name, _NAVAL):
        return "naval"
    if _has(name, _RANGED):
        return "ranged"
    if _has(name, _CAVALRY):
        return "cavalry"
    if _has(name, _AIR):
        return "air"
    if _has(name, _RECON):
        return "recon"
    if ranged > 0:
        return "ranged"
    return "melee"


_MILITARY = ("melee", "ranged", "siege", "cavalry", "naval", "air", "recon")


def summarize_units(units: list) -> dict:
    counts = {}
    combats = []
    settlers = builders = 0
    for u in units:
        cat = classify_unit(u)
        counts[cat] = counts.get(cat, 0) + 1
        name = str(u.get("type", "")).upper()
        if "SETTLER" in name:
            settlers += 1
        elif "BUILDER" in name:
            builders += 1
        if cat in _MILITARY:
            combats.append(_num(u.get("combat")))
    military = sum(counts.get(c, 0) for c in _MILITARY)
    max_combat = max(combats) if combats else 0.0
    outdated = sum(1 for cv in combats if cv > 0 and cv < max_combat * 0.6)
    return {
        "counts": counts,
        "military": military,
        "has_siege": counts.get("siege", 0) > 0,
        "ranged": counts.get("ranged", 0),
        "cavalry": counts.get("cavalry", 0),
        "melee": counts.get("melee", 0),
        "naval": counts.get("naval", 0),
        "settlers": settlers,
        "builders": builders,
        "max_combat": max_combat,
        "outdated": outdated,
    }


# ── Assemble the snapshot into a state dict ──────────────────────────────────

def build_state(turn, player: dict, cities: list, units: list,
                rivals: list, citystates: list) -> dict:
    state = dict(player or {})           # legacy flat fields (counts, yields, …)
    if "turn" not in state and turn is not None:
        state["turn"] = turn

    state["cityData"]   = cities or []         # rich per-city (districts, loyalty, …)
    state["rivals"]     = rivals or []         # rich per-rival (state, ally, yields)
    state["cityStates"] = citystates or []
    state["unitList"]   = units or []
    state["unitSummary"] = summarize_units(units or [])

    # threats[] derived from per-city nearest-enemy info
    threats = []
    for c in (cities or []):
        d = _num(c.get("threatDist"), 99)
        if d <= 8:
            threats.append({"city": c.get("name", "a city"),
                            "dist": d, "owner": c.get("threatOwner", ""),
                            "count": int(_num(c.get("threatCount")))})
    state["threats"] = threats
    return state
