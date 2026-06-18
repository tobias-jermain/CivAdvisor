"""
CivAdvisor — Local Weighted Advisor Engine (full-tracking build)
================================================================

Deterministic, offline. No network, cannot be rate-limited.

Produces a full turn REPORT:
  - victory progress across all paths (Science/Culture/Domination/Religion/Diplomacy)
  - your strongest path + how each rival threatens you
  - threat intelligence from enemy unit positions near your cities
  - a ranked, weighted list of actionable tips, tailored to your leader & era

`compute_report(state)` is the main entry. `compute_tips(state)` is a thin
wrapper that returns just the tip list (kept for compatibility).

Tip weights are 0-100: higher = more urgent / shown first.
"warn" = problem to fix, "good" = doing well, "info" = directional nudge.

NOTE ON ACCURACY: Civ VI's Lua does not cleanly expose the game's internal
victory percentages, so victory "progress" here is a best-effort estimate from
the signals the mod *can* read (techs completed, original capitals held,
religious spread, tourism/yield rankings vs rivals). Treat the bars as guidance,
not the official victory screen.
"""

from __future__ import annotations
from typing import Callable, Optional

MAX_TIPS = 5
MIN_SHOW = 42          # tips below this weight are noise — hidden unless nothing else
ALWAYS_SHOW = 2        # but always show at least this many

# Approx counts for progress normalisation (GS ruleset, base trees).
TOTAL_TECHS  = 76
TOTAL_CIVICS = 50
DIPLO_VICTORY_POINTS = 20


# ── Expert knowledge tables ───────────────────────────────────────────────────

EUREKA_TRIGGERS: dict = {
    "POTTERY":           "Build a Farm on any tile",
    "ANIMAL_HUSBANDRY":  "Build a Pasture improvement",
    "MINING":            "Improve a resource with a Mine",
    "SAILING":           "Found a city on a Coast tile",
    "ASTROLOGY":         "Discover a Natural Wonder with any unit",
    "BRONZE_WORKING":    "Kill 3 Barbarian units",
    "IRRIGATION":        "Farm a resource requiring Irrigation (Rice, Wheat, or Sugar)",
    "WRITING":           "Meet 3 City-States",
    "ARCHERY":           "Kill a unit with a Slinger",
    "MASONRY":           "Build a Quarry improvement",
    "EARLY_EMPIRE":      "Grow your total empire Population to 6",
    "HORSEBACK_RIDING":  "Build a Stable in any city",
    "CURRENCY":          "Build 2 Market buildings",
    "IRON_WORKING":      "Build an Encampment district",
    "ENGINEERING":       "Build Ancient Walls in any city",
    "MATHEMATICS":       "Build 3 specialty districts total",
    "SHIPBUILDING":      "Build 2 Galley units",
    "MILITARY_TACTICS":  "Kill a unit with a Spearman",
    "APPRENTICESHIP":    "Build 3 Mine improvements",
    "STIRRUPS":          "Have a Medieval-era or later government",
    "MACHINERY":         "Build a Lumber Mill improvement",
    "FEUDALISM":         "Build 6 Farm improvements",
    "MASS_PRODUCTION":   "Build a Shipyard in any city",
    "GUNPOWDER":         "Build an Armory in any city",
    "SQUARE_RIGGING":    "Kill a unit with a Musketman",
    "ASTRONOMY":         "Build a University adjacent to a Mountain tile",
    "PRINTING":          "Build 2 University buildings",
    "SCIENTIFIC_THEORY": "Have a Renaissance-era or later government",
    "INDUSTRIALIZATION": "Build 3 Workshop buildings",
    "STEAM_POWER":       "Build 2 Harbor districts",
    "BALLISTICS":        "Build 2 Armory buildings",
    "MILITARY_SCIENCE":  "Kill a unit with a Cavalry unit",
    "RIFLING":           "Build a Barracks and a Stable in the same city",
    "ELECTRICITY":       "Build a Dam improvement",
    "COMBUSTION":        "Discover 3 Oil resources on the map",
    "FLIGHT":            "Build an Aerodrome district",
    "RADIO":             "Establish a National Park",
    "ROCKETRY":          "Build a Research Lab",
    "ADVANCED_FLIGHT":   "Build 3 Biplane units",
    "COMPUTERS":         "Build a Research Lab in any Campus city",
    "SATELLITES":        "Launch an Earth Satellite city project",
    "ROBOTICS":          "Build a Spaceport district",
    "NUCLEAR_FISSION":   "Build 2 Power Plants of any type",
}

INSPIRATION_TRIGGERS: dict = {
    "CODE_OF_LAWS":         "Meet any other civilization",
    "CRAFTSMANSHIP":        "Improve 3 tiles with a Builder",
    "FOREIGN_TRADE":        "Meet any other civilization",
    "EARLY_EMPIRE":         "Grow your Capital to Population 6",
    "MYSTICISM":            "Found a Pantheon",
    "STATE_WORKFORCE":      "Build any specialty district",
    "POLITICAL_PHILOSOPHY": "Meet 3 City-States",
    "GAMES_AND_RECREATION": "Build 3 different specialty districts",
    "DRAMA_AND_POETRY":     "Build any Theatre Square district building",
    "RECORDED_HISTORY":     "Found or conquer 3 cities total",
    "DEFENSIVE_TACTICS":    "Have a completed Encampment district",
    "MILITARY_TRADITION":   "Earn any unit promotion",
    "FEUDALISM":            "Build 6 Farm improvements",
    "CIVIL_SERVICE":        "Have a city on a River that contains a Granary",
    "DIPLOMATIC_LEAGUE":    "Send a Trade Route to another civilization's city",
    "GUILDS":               "Build a Commercial Hub and a Harbor in the same city",
    "MEDIEVAL_FAIRES":      "Build a Market in a city receiving an international Trade Route",
    "THEOLOGY":             "Have a Holy City in your territory",
    "DIVINE_RIGHT":         "Build 2 Temple buildings",
    "REFORMED_CHURCH":      "Have 3 cities following your Religion",
    "HUMANISM":             "Recruit or patronize any Great Person",
    "MERCANTILISM":         "Acquire any Great Work",
    "THE_ENLIGHTENMENT":    "Declare Friendship with 3 civilizations",
    "NATURAL_HISTORY":      "Build a Zoo",
    "URBANIZATION":         "Build a Commercial Hub and a Theatre Square in the same city",
    "CONSERVATION":         "Have a city with both an Aqueduct and a Theatre Square",
    "GLOBALIZATION":        "Build 2 Airports",
    "SOCIAL_MEDIA":         "Have a city following your Religion on every continent you control",
    "SUFFRAGE":             "Have any city reach Population 15",
    "CLASS_STRUGGLE":       "Have 3 Industrial Zones with Factory buildings",
    "COLD_WAR":             "Research Nuclear Fission",
}

# City-state bonuses: keyed by uppercase underscore name. "tier" S > A > B.
CS_BONUS_DATA: dict = {
    "GENEVA":        {"bonus": "+15% Science in all your cities (requires no active war)",
                      "victory": ["science"], "tier": "S"},
    "YEREVAN":       {"bonus": "choose any promotion for Missionaries and Apostles you train — ignore normal unlock order",
                      "victory": ["religion"], "tier": "S"},
    "KUMASI":        {"bonus": "+2 Culture and +1 Gold per specialty district in your trade-route origin city",
                      "victory": ["culture"], "tier": "S"},
    "ZANZIBAR":      {"bonus": "grants Cloves and Cinnamon to your empire (+12 Amenities total)",
                      "victory": ["any"], "tier": "S"},
    "BOLOGNA":       {"bonus": "receive one free building in a new era's class every era",
                      "victory": ["science", "culture"], "tier": "S"},
    "HONG_KONG":     {"bonus": "+20% Production toward city projects in all your cities",
                      "victory": ["science", "domination"], "tier": "A"},
    "SINGAPORE":     {"bonus": "+2 Production in all cities per Trade Route you send to another civ or CS",
                      "victory": ["science", "culture"], "tier": "A"},
    "AUCKLAND":      {"bonus": "Fishing Boats yield +1 Food +1 Production, plus an extra +1 Food each era",
                      "victory": ["science", "culture"], "tier": "A"},
    "TORONTO":       {"bonus": "+10% Production toward wonders and districts in cities with 3+ specialty districts",
                      "victory": ["science", "culture"], "tier": "A"},
    "VILNIUS":       {"bonus": "instantly applies +100% Science per era elapsed to your current tech once",
                      "victory": ["science"], "tier": "A"},
    "NALANDA":       {"bonus": "Builders construct Holy Sites instantly; Holy Site buildings cost 50% less",
                      "victory": ["religion", "science"], "tier": "A"},
    "AMSTERDAM":     {"bonus": "Trade Routes to cities with Luxury/Strategic resources grant you those resources",
                      "victory": ["any"], "tier": "A"},
    "ANTANANARIVO":  {"bonus": "+2% Culture (scaling) per citizen in your most productive cultural city",
                      "victory": ["culture"], "tier": "A"},
    "NAN_MADOL":     {"bonus": "+2 Culture to all Coast and Lake tiles in cities with a district",
                      "victory": ["culture"], "tier": "A"},
    "ARMAGH":        {"bonus": "Builders can construct Monasteries (faith buildings) on any tile",
                      "victory": ["religion"], "tier": "A"},
    "VATICAN":       {"bonus": "Missionaries and Apostles spread 50% more Religious Pressure per action",
                      "victory": ["religion"], "tier": "A"},
    "MEXICO_CITY":   {"bonus": "improvements on Natural Wonder tiles provide +2 of all yields",
                      "victory": ["culture"], "tier": "A"},
    "PRESLAV":       {"bonus": "international Trade Routes from your cities provide +3 Culture each",
                      "victory": ["culture"], "tier": "B"},
    "HUNZA":         {"bonus": "Trade Routes sent to city-states provide +4 Gold",
                      "victory": ["religion", "culture"], "tier": "B"},
    "CHINGUETTI":    {"bonus": "Trade Routes from your cities provide +2 Faith each",
                      "victory": ["religion"], "tier": "B"},
    "MOHENJO_DARO":  {"bonus": "all cities with no adjacent improvements get +2 Housing",
                      "victory": ["science", "culture"], "tier": "B"},
}

# Dark Age policy cards by era index (era the Dark Age is entered from).
_DARK_AGE_CARDS: dict = {
    0: [("Twilight Valor",    "Military",  "+5 Combat Strength for your units while in own territory"),
        ("Professional Army", "Military",  "unit upgrades cost 50% less Gold"),
        ("Isolationism",      "Economic",  "+2 Food +2 Production from every internal Trade Route"),
        ("Scripture",         "Economic",  "+100% Faith from all Holy Site buildings"),
        ("Monasticism",       "Wildcard",  "+75% Science in Holy Site cities; −25% Culture empire-wide")],
    1: [("Twilight Valor",    "Military",  "+5 Combat Strength for your units while in own territory"),
        ("Professional Army", "Military",  "unit upgrades cost 50% less Gold"),
        ("Isolationism",      "Economic",  "+2 Food +2 Production from every internal Trade Route"),
        ("Scripture",         "Economic",  "+100% Faith from all Holy Site buildings"),
        ("Monasticism",       "Wildcard",  "+75% Science in Holy Site cities; −25% Culture empire-wide")],
    2: [("Twilight Valor",    "Military",  "+5 Combat Strength for your units while in own territory"),
        ("Professional Army", "Military",  "unit upgrades cost 50% less Gold"),
        ("Isolationism",      "Economic",  "+2 Food +2 Production from every internal Trade Route"),
        ("Monasticism",       "Wildcard",  "+75% Science in Holy Site cities; −25% Culture empire-wide")],
    3: [("Twilight Valor",    "Military",  "+5 Combat Strength for your units while in own territory"),
        ("Professional Army", "Military",  "unit upgrades cost 50% less Gold"),
        ("Isolationism",      "Economic",  "+2 Food +2 Production from every internal Trade Route")],
}

_HEROIC_DEDICATIONS: dict = {
    0: ("Monumentality",
        "Faith-buy Settlers, Builders, and Traders at 25% off — 3 picks lets you snowball expansion and infrastructure instantly"),
    1: ("Free Inquiry",
        "all Great Person tile improvements give +1 Science +1 Production — flood GP points and recruit aggressively"),
    2: ("Free Inquiry or Exodus of the Evangelists",
        "science GP sprint, or faith-buy Apostles/Missionaries 25% cheaper to lock down religious dominance"),
    3: ("To Arms! or Free Inquiry",
        "slash unit upgrade costs with your gold surplus, or push a science Great Person rush — 3 picks compounds everything"),
}

# High-value policy cards: (min_era_idx, slot, name, effect, focus_hint)
_HIGH_VALUE_CARDS: list = [
    (0, "Economic", "Triangular Trade",
     "+4 Gold +1 Faith per Trade Route — highest raw gold yield of any econ card for most of the game",
     "any"),
    (1, "Economic", "Rationalism",
     "+50% Science in Campuses with 3+ adjacency bonus, or in any city with 10+ population",
     "science"),
    (1, "Economic", "Market Economy",
     "+2 Gold per Market and Harbor in every city",
     "any"),
    (2, "Wildcard", "Simultaneum",
     "Religious buildings can be built in any city regardless of dominant faith — spreads infrastructure fast",
     "religion"),
    (3, "Military", "Levée en Masse",
     "+10 Combat Strength and +1 Movement for all Corps and Armies",
     "domination"),
    (3, "Economic", "Reform the Coinage",
     "+0.5 Gold per citizen across all cities — massive in a tall or large empire",
     "any"),
    (4, "Economic", "Five-Year Plan",
     "+2 Production per Industrial Zone and Neighborhood in every city",
     "science"),
    (5, "Wildcard", "Space Race",
     "+5% Production on Space Race projects per city with a Spaceport",
     "science"),
    (5, "Wildcard", "Satellite Broadcasts",
     "+2 Tourism from Seaside Resorts; +1 Culture +1 Tourism from National Parks",
     "culture"),
    (6, "Wildcard", "Online Communities",
     "+100% Tourism from National Parks, World Wonders, and Natural Wonders — often decisive for a late culture win",
     "culture"),
]


# ── Helpers ─────────────────────────────────────────────────────────────────

def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def pretty_leader(token: str) -> str:
    """LEADER_TEDDY_ROOSEVELT -> 'Teddy Roosevelt'."""
    if not token:
        return "a rival"
    prof = CIV_PROFILES.get(token)
    if prof and "—" in prof["name"]:
        return prof["name"].split("—", 1)[1].strip()
    t = token
    for pre in ("LEADER_", "CIVILIZATION_"):
        if t.startswith(pre):
            t = t[len(pre):]
    return t.replace("_", " ").title()


def _rank(mine: float, others: list) -> tuple:
    """Return (rank, total) where rank 1 = best. `others` = rival values."""
    total = 1 + len(others)
    ahead = sum(1 for v in others if v > mine)
    return ahead + 1, total


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _clean_name(s: str) -> str:
    """TECH_IRON_WORKING → 'Iron Working'."""
    for prefix in ("TECH_", "CIVIC_"):
        if s.upper().startswith(prefix):
            s = s[len(prefix):]
    return s.replace("_", " ").title()


def _eureka_trigger(name: str) -> str:
    up = name.upper().replace(" ", "_")
    for key, trigger in EUREKA_TRIGGERS.items():
        if key in up or up in key:
            return trigger
    return ""


def _inspiration_trigger(name: str) -> str:
    up = name.upper().replace(" ", "_")
    for key, trigger in INSPIRATION_TRIGGERS.items():
        if key in up or up in key:
            return trigger
    return ""


def _cs_data(name: str) -> dict:
    up = name.upper().replace(" ", "_").replace("-", "_")
    for key, data in CS_BONUS_DATA.items():
        if key in up or up in key:
            return data
    return {}


# ── Context: derive every signal from the raw state ─────────────────────────

def _context(state: dict) -> dict:
    c = {}
    c["turn"]        = int(_num(state.get("turn", 0)))
    c["era"]         = str(state.get("era", "unknown"))
    c["era_idx"]     = int(_num(state.get("eraIndex", 0)))
    c["civ"]         = str(state.get("civ", "unknown"))
    c["leader"]      = str(state.get("leader", "unknown"))
    c["score"]       = _num(state.get("score"))

    c["gold"]        = _num(state.get("gold"))
    c["gpt"]         = _num(state.get("gpt"))
    c["maintenance"] = _num(state.get("maintenance"))

    c["science"]     = _num(state.get("science"))
    c["culture"]     = _num(state.get("culture"))
    c["faith"]       = _num(state.get("faith"))
    c["fpt"]         = _num(state.get("fpt"))
    c["faith_bal"]   = _num(state.get("faithBalance"))
    c["tourism"]     = _num(state.get("tourism"))

    c["tech"]        = str(state.get("currentTech", "none"))
    c["tech_turns"]  = _num(state.get("techTurns"))
    c["techs_done"]  = _num(state.get("techsDone"))
    c["civic"]       = str(state.get("currentCivic", "none"))
    c["civic_turns"] = _num(state.get("civicTurns"))
    c["civics_done"] = _num(state.get("civicsDone"))

    c["cities"]      = int(_num(state.get("cities")))
    c["units"]       = int(_num(state.get("units")))
    c["policies"]    = int(_num(state.get("policies")))
    c["my_military"] = _num(state.get("myMilitary"))

    c["founded_rel"]   = int(_num(state.get("foundedReligion")))
    c["my_rel_cities"] = _num(state.get("myReligionCities"))
    c["world_cities"]  = _num(state.get("worldCities"))
    c["orig_caps"]     = _num(state.get("origCapsHeld"))
    c["total_majors"]  = _num(state.get("totalMajors"))
    c["diplo_pts"]     = _num(state.get("diploPoints"))

    # Cities
    city_data = state.get("cityData") or []
    c["city_data"] = city_data
    idle, starving, unhappy, capped = [], [], [], []
    total_prod = 0.0
    for cd in city_data:
        name  = cd.get("name", "a city")
        bld   = str(cd.get("building", "none")).strip().lower()
        food  = _num(cd.get("food"))
        amen  = _num(cd.get("amenities"))
        pop   = _num(cd.get("pop"))
        house = _num(cd.get("housing"))
        total_prod += _num(cd.get("prod"))
        if bld in ("none", "unknown", "", "nothing"):
            idle.append(name)
        if food <= 0:
            starving.append(name)
        if amen <= -1:
            unhappy.append((name, amen))
        if pop - house >= -1:
            capped.append(name)
    c["idle_cities"]     = idle
    c["starving_cities"] = starving
    c["unhappy_cities"]  = unhappy
    c["capped_cities"]   = capped
    c["total_prod"]      = total_prod

    # Rivals (met majors) — full stat line each
    rivals = state.get("rivals") or []
    c["rivals"]      = rivals
    c["at_war_with"] = [r for r in rivals if _num(r.get("atWar")) > 0]

    # War standing — used to keep aggressive vs defensive advice from clashing.
    my_mil = _num(state.get("myMilitary"))
    c["losing_war"] = any(_num(r.get("military")) > my_mil * 1.3 for r in c["at_war_with"]) if my_mil > 0 else bool(c["at_war_with"])
    c["winning_war"] = any(my_mil > _num(r.get("military")) * 1.2 for r in c["at_war_with"])

    def col(key):
        return [_num(r.get(key)) for r in rivals]
    c["rv_military"] = col("military")
    c["rv_science"]  = col("science")
    c["rv_culture"]  = col("culture")
    c["rv_faith"]    = col("faith")
    c["rv_score"]    = col("score")
    c["rv_tourism"]  = col("tourism")

    def top(key):
        best = None
        for r in rivals:
            if best is None or _num(r.get(key)) > _num(best.get(key)):
                best = r
        return best
    c["top_military"] = top("military")
    c["top_science"]  = top("science")
    c["top_culture"]  = top("culture")
    c["top_faith"]    = top("faith")
    c["top_score"]    = top("score")
    c["top_tourism"]  = top("tourism")

    # Threats (enemy units near our cities)
    c["threats"] = state.get("threats") or []

    # ── Full-board fields (present only with the full-board Lua exporter) ─────
    c["unit_summary"] = state.get("unitSummary") or {}
    c["strategics"]   = state.get("strategics") or {}
    c["city_states"]  = state.get("cityStates") or []
    c["era_score"]    = _num(state.get("eraScore"))
    c["age"]          = str(state.get("age", "normal"))
    c["government"]   = str(state.get("government", "none"))
    c["trade_used"]   = _num(state.get("tradeUsed"))
    c["trade_cap"]    = _num(state.get("tradeCap"))
    c["boost_tech"]   = int(_num(state.get("boostTech")))
    c["boost_civic"]  = int(_num(state.get("boostCivic")))
    # Was this state produced by the full-board exporter? (gates the new rules)
    c["full_board"]   = "unitSummary" in state or "cityStates" in state

    return c


# ── Victory tracking ────────────────────────────────────────────────────────

def _victories(c) -> list:
    """Estimate progress/standing for every victory path."""
    out = []

    # Science — techs completed + science-yield standing
    s_rank, s_tot = _rank(c["science"], c["rv_science"])
    out.append({
        "key": "science", "name": "Science",
        "pct": _clamp01(c["techs_done"] / TOTAL_TECHS) if c["techs_done"] else 0.0,
        "rank": s_rank, "total": s_tot,
        "metric": c["science"],
        "leading": s_rank == 1,
        "note": f"#{s_rank}/{s_tot} science · {int(c['techs_done'])} techs",
    })

    # Culture — tourism/culture standing (proxy; true win needs foreign tourists)
    cul_metric = c["culture"] + c["tourism"]
    cul_rivals = [a + b for a, b in zip(c["rv_culture"], c["rv_tourism"])]
    cu_rank, cu_tot = _rank(cul_metric, cul_rivals)
    out.append({
        "key": "culture", "name": "Culture",
        "pct": _clamp01(c["tourism"] / 100.0),
        "rank": cu_rank, "total": cu_tot,
        "metric": cul_metric,
        "leading": cu_rank == 1,
        "note": f"#{cu_rank}/{cu_tot} culture · {int(c['tourism'])} tourism/t",
    })

    # Domination — progress is CAPTURED enemy original capitals only. Your own
    # capital doesn't count (you start with it), so this reads 0% until you
    # actually take someone else's, instead of a misleading 16% on turn 1.
    captured = max(0, int(c["orig_caps"]) - 1)          # exclude your own capital
    needed   = max(1, int(c["total_majors"]) - 1)       # every other major's capital
    dom_pct  = _clamp01(captured / needed)
    m_rank, m_tot = _rank(c["my_military"], c["rv_military"])
    out.append({
        "key": "domination", "name": "Domination",
        "pct": dom_pct,
        "rank": m_rank, "total": m_tot,
        "metric": c["my_military"],
        "leading": m_rank == 1,
        "note": f"#{m_rank}/{m_tot} army · {captured} enemy capital{'s' if captured != 1 else ''} taken",
    })

    # Religion — share of world cities following your faith
    rel_pct = _clamp01(c["my_rel_cities"] / c["world_cities"]) if c["world_cities"] else 0.0
    f_rank, f_tot = _rank(c["faith"], c["rv_faith"])
    out.append({
        "key": "religion", "name": "Religion",
        "pct": rel_pct if c["founded_rel"] else 0.0,
        "rank": f_rank, "total": f_tot,
        "metric": c["faith"],
        "leading": c["founded_rel"] == 1 and f_rank == 1,
        "note": (f"{int(c['my_rel_cities'])}/{int(c['world_cities'])} cities converted"
                 if c["founded_rel"] else "no religion founded"),
    })

    # Diplomacy — diplomatic victory points (GS)
    out.append({
        "key": "diplomacy", "name": "Diplomacy",
        "pct": _clamp01(c["diplo_pts"] / DIPLO_VICTORY_POINTS),
        "rank": 0, "total": 0,
        "metric": c["diplo_pts"],
        "leading": False,
        "note": f"{int(c['diplo_pts'])}/{DIPLO_VICTORY_POINTS} diplo points",
    })

    return out


def _strongest_path(c, victories, focus=None) -> dict:
    """Pick the victory to steer toward.

    If the player has chosen a focus (anything but 'auto'), honour it directly —
    the engine then pushes that path regardless of civ. Otherwise auto-pick the
    one they're best positioned for, blending progress, standing and civ uniques.
    """
    if focus and focus != "auto":
        for v in victories:
            if v["key"] == focus:
                return v
    lean = CIV_PROFILES.get(c["leader"], {}).get("focus", "")
    best, best_score = victories[0], -1.0
    for v in victories:
        # position score: progress + standing bonus + small civ-affinity nudge
        rank_bonus = 0.0
        if v["total"]:
            rank_bonus = (v["total"] - v["rank"] + 1) / v["total"]
        affinity = 0.15 if v["key"] in lean else 0.0
        score = v["pct"] * 0.6 + rank_bonus * 0.4 + affinity
        if score > best_score:
            best, best_score = v, score
    return best


VICTORY_PLAYBOOK = {
    "science":   ("Go for Science",
                  "Campuses on mountain/reef adjacency in every city, grab Great Library + Oxford, run the Rationalism card. Beeline Apprenticeship then the space-race techs. If a rival out-techs you, war them to slow it — don't just lose the race."),
    "culture":   ("Go for Culture",
                  "Theatre Squares everywhere, fill AND theme every Great Work slot, snipe culture wonders. Then weaponise it: rush Flight → Conservation → Computers and spam Seaside Resorts + National Parks. Raw culture doesn't win — tourism does."),
    "domination":("Go for Domination",
                  "Stay a full era ahead on units and ALWAYS bring 2+ siege (catapults → bombards → artillery) — melee can't crack walls. Hit a neighbour before they get Walls/Crossbows, take capitals one at a time, and keep the captured cities."),
    "religion":  ("Go for Religion",
                  "Faith-buy Apostles in bulk, take Debater/Translator promotions, and convert enemy cities one by one while Inquisitors guard your holy cities. Religious combat converts far faster than missionary spam."),
    "diplomacy": ("Go for Diplomacy",
                  "Hoard Diplomatic Favour, always back yourself in World Congress votes, and farm Aid Requests / emergencies. Trade surplus resources for favour, then buy the win at the vote."),
}


# ── Generic rules ───────────────────────────────────────────────────────────

def rule_economy(c) -> list:
    out = []
    gpt, gold = c["gpt"], c["gold"]
    if gpt < 0:
        runway = (gold / -gpt) if gpt < 0 else 999
        if runway <= 5:
            out.append({"type": "warn", "weight": 96, "title": "Going bankrupt",
                "body": f"Net {gpt:+.0f} gold/turn, {gold:.0f} banked — ~{runway:.0f} turns to zero. At 0 gold the game "
                        "disbands your units. Cut maintenance or boost trade now."})
        else:
            out.append({"type": "warn", "weight": 62, "title": "Gold draining",
                "body": f"Losing {gpt:+.0f}/turn. Build a Commercial Hub, send a trade route, or sell a luxury before the buffer runs out."})
    elif gold > 350 and c["turn"] < 120:
        out.append({"type": "info", "weight": 42, "title": "Gold sitting idle", "tab": "plan",
            "body": f"{gold:.0f} gold unspent. Early gold is worth most spent now — buy a Settler, Builder, or a key tile to snowball."})
    return out


def rule_science(c) -> list:
    out = []
    if c["tech"].lower() in ("none", "unknown"):
        out.append({"type": "warn", "weight": 90, "title": "No research selected", "tab": "now",
            "body": "Your tech queue is empty — free progress wasted every turn. Pick a technology."})
    elif c["era_idx"] >= 1 and c["turn"] > 40 and c["science"] < max(1.0, c["turn"] * 0.5):
        out.append({"type": "info", "weight": 50, "title": "Science is lagging",
            "body": f"Only {c['science']:.0f} science/turn at turn {c['turn']}. Add Campuses and work jungle/coast — falling behind tech compounds badly."})
    return out


def rule_culture(c) -> list:
    out = []
    if c["civic"].lower() in ("none", "unknown"):
        out.append({"type": "warn", "weight": 88, "title": "No civic selected", "tab": "now",
            "body": "Civic queue is empty — you're losing culture progress and policy unlocks. Pick a civic."})
    elif c["era_idx"] >= 1 and c["turn"] > 40 and c["culture"] < max(1.0, c["turn"] * 0.32):
        out.append({"type": "info", "weight": 44, "title": "Culture is thin",
            "body": f"{c['culture']:.0f} culture/turn is low — slower policies and wonders. A Theatre Square or Monument helps."})
    return out


def rule_production(c) -> list:
    idle = c["idle_cities"]
    if idle:
        names = ", ".join(idle[:3]) + ("…" if len(idle) > 3 else "")
        return [{"type": "warn", "weight": 86, "title": f"Idle production ({len(idle)})",
            "body": f"Nothing queued in {names}. Idle production is the biggest tempo loss — queue a district, builder, or unit."}]
    return []


def rule_growth(c) -> list:
    out = []
    if c["starving_cities"]:
        out.append({"type": "warn", "weight": 80, "title": "Cities starving", "tab": "now",
            "body": (f"{', '.join(c['starving_cities'][:3])} have zero food surplus and will shrink. "
                     "Work more food tiles (flat Grassland +2, Farms add +1 extra after Feudalism), "
                     "or fix amenities — unhappy cities halve their growth.")})
    if c["capped_cities"]:
        era = c["era_idx"]
        names = ", ".join(c["capped_cities"][:3])
        if era <= 0:
            fix = "Build a Granary (+2 Housing, available immediately)"
        elif era <= 2:
            fix = ("Build an Aqueduct district (+6 Housing if placed adjacent to a River, Lake, or Mountain — "
                   "vastly better than a Granary and available from Classical era)")
        else:
            fix = ("Build a Neighborhood district — its Housing scales with city size and has no cap. "
                   "Neighborhoods also add Appeal to surrounding tiles")
        out.append({"type": "warn", "weight": 64, "title": "Growth stalled (housing)",
            "body": f"{names} hit the housing cap — growth is 50% penalised. {fix}."})
    return out


def rule_amenities(c) -> list:
    out = []
    for name, amen in c["unhappy_cities"]:
        if amen <= -3:
            out.append({"type": "warn", "weight": 82, "title": f"{name} near revolt", "tab": "now",
                "body": f"{name} is at {amen:.0f} amenities — big yield penalties and revolt risk. Get a luxury or Entertainment Complex fast."})
        else:
            out.append({"type": "warn", "weight": 68, "title": f"{name} unhappy",
                "body": f"{name} is short on amenities ({amen:.0f}). Connect a luxury or build entertainment."})
    return out[:2]


def rule_expansion(c) -> list:
    """Mid-game catch: you're meaningfully BEHIND the normal city-count curve.
    Early-game expansion is encouraged positively by the phase playbook instead."""
    t, cities = c["turn"], c["cities"]
    if t < 30 or cities >= 6:
        return []
    # Rough healthy curve: ~1 new city every 16 turns after turn 12.
    expected = 1 + max(0, (t - 12) // 16)
    if cities >= expected:
        return []
    deficit = expected - cities
    weight = 58 if deficit >= 2 else 46
    return [{"type": "info", "weight": weight, "title": "Room to expand",
        "body": f"{cities} cities at turn {t} is below the curve for this point. A Settler or two now "
                "compounds all game — grab land while it's open."}]


def rule_military(c) -> list:
    if c["units"] == 0:
        return [{"type": "warn", "weight": 84, "title": "No military units",
            "body": "No units on the map — one barbarian camp or surprise war is a disaster. Build a couple of cheap units."}]
    if c["units"] <= 1 and c["turn"] > 15:
        return [{"type": "warn", "weight": 66, "title": "Dangerously few units",
            "body": "One unit can't defend and explore. Add a Warrior/Slinger and a Scout."}]
    return []


def rule_faith(c) -> list:
    if c["faith_bal"] > 350 and c["fpt"] > 0:
        return [{"type": "info", "weight": 36, "title": "Faith piling up",
            "body": f"{c['faith_bal']:.0f} faith banked. Spend it — Great People, missionaries, or faith-bought units/buildings."}]
    return []


def rule_threats(c) -> list:
    """Enemy unit positions near our cities — the 'use enemy positions' part."""
    out = []
    at_war_leaders = {pretty_leader(r.get("leader", "")) for r in c["at_war_with"]}
    for th in c["threats"][:3]:
        city  = th.get("city", "a city")
        dist  = _num(th.get("dist"))
        owner = pretty_leader(th.get("owner", ""))
        count = int(_num(th.get("count")))
        hostile = owner in at_war_leaders
        if dist <= 2 and (hostile or count >= 2):
            out.append({"type": "warn", "weight": 94 if hostile else 72,
                "title": f"{city} under threat",
                "body": f"{count} of {owner}'s units are {int(dist)} tile(s) from {city}. "
                        + ("You're at war — garrison it, rush-buy a defender, and build/man the walls now."
                           if hostile else "Not at war yet, but position a defender and watch for a surprise declaration.")})
        elif dist <= 4 and count >= 3:
            out.append({"type": "info", "weight": 58,
                "title": f"Army massing near {city}",
                "body": f"{owner} has {count} units within {int(dist)} tiles of {city}. Pre-build walls and a defender before it becomes a problem."})
    return out


def rule_diplomacy(c) -> list:
    """War advice, gated on the strength ratio so it's never contradictory:
    losing -> defend/peace, winning -> press hard, even -> fight smart."""
    out = []
    my = c["my_military"]
    for r in c["at_war_with"]:
        enemy = pretty_leader(r.get("leader", ""))
        em = _num(r.get("military"))
        if my > 0 and em > my * 1.3:
            out.append({"type": "warn", "weight": 92, "title": f"Losing war vs {enemy}",
                "body": f"Their army (~{em:.0f}) beats yours (~{my:.0f}). Pull units into your cities, man the walls with ranged, rush-buy a defender or two, and take the peace deal once you've bled them at your walls."})
        elif my > em * 1.2:
            out.append({"type": "warn", "weight": 82, "title": f"Press the attack on {enemy}",
                "body": f"You out-muscle {enemy} (~{my:.0f} vs ~{em:.0f}) — do NOT sign a white peace. Push to their nearest city, soften walls with siege, and take it. Make the war pay with a captured city before you stop."})
        else:
            out.append({"type": "warn", "weight": 78, "title": f"Even war with {enemy}",
                "body": f"Roughly matched (~{my:.0f} vs ~{em:.0f}). Fight defensively near your own cities where walls + ranged give you the edge, and only push where you have local numbers."})

    sr = c["top_military"]
    if sr and not c["at_war_with"] and my > 0 and _num(sr.get("military")) > my * 1.5:
        out.append({"type": "warn", "weight": 64, "title": f"{pretty_leader(sr.get('leader',''))} can crush you",
            "body": f"Their army (~{_num(sr.get('military')):.0f}) dwarfs yours (~{my:.0f}). Get Walls up in border cities and a couple of current-era units NOW, or you'll lose a city to a surprise war."})
    return out


def rule_war_opportunity(c) -> list:
    """Aggressive, data-driven: if you clearly out-muscle a neighbour you're at
    peace with, take the window. Suppressed if you're already at war or have a
    hostile army at your door (so it never fights the 'defend' advice)."""
    my = c["my_military"]
    if my <= 0 or c["at_war_with"]:
        return []
    if any(_num(t.get("dist")) <= 3 for t in c["threats"]):
        return []                      # someone's already on you — don't open a second front
    peace_rivals = [r for r in c["rivals"] if not _num(r.get("atWar"))]
    if not peace_rivals:
        return []
    weakest = min(peace_rivals, key=lambda r: _num(r.get("military")))
    wm = _num(weakest.get("military"))
    if my < max(wm * 1.4, wm + 40):    # need a real edge, not a coin-flip
        return []
    enemy = pretty_leader(weakest.get("leader", ""))
    dom = c.get("focus") == "domination"
    early = c["era_idx"] <= 2
    weight = 74 if dom else (62 if early else 54)
    extra = " This is your unique-unit window — it won't be this easy later." if early else ""
    return [{"type": "info", "weight": weight, "title": f"Strike {enemy} now",
        "body": f"You out-muscle {enemy} (~{my:.0f} vs ~{wm:.0f}) and you're at peace — that's a wasted edge. "
                f"Mass 2+ siege units, declare, and take a city or their capital.{extra} A captured city beats 40 turns of building."}]


def rule_rival_victory_threat(c, victories) -> list:
    """Warn when a rival is clearly leading a victory path — and how to stop them."""
    out = []
    can_fight = c["my_military"] > 0
    # Science
    ts = c["top_science"]
    if ts and _num(ts.get("science")) > c["science"] * 1.4 and _num(ts.get("science")) > 0:
        kill = " You out-gun them — a war to burn their Campuses/capital is the surest answer." if can_fight and _num(ts.get("military")) < c["my_military"] else " Out-build them: Campuses on best adjacency + Rationalism."
        out.append({"type": "warn", "weight": 60, "title": f"{pretty_leader(ts.get('leader',''))} is winning the tech race",
            "body": f"Their science (~{_num(ts.get('science')):.0f}) is well ahead of yours (~{c['science']:.0f}).{kill}"})
    # Culture
    tc = c["top_tourism"]
    if tc and _num(tc.get("tourism")) > max(c["tourism"], 5) * 1.5:
        out.append({"type": "warn", "weight": 58, "title": f"{pretty_leader(tc.get('leader',''))} is winning on tourism",
            "body": f"They're pulling away on a Culture win (~{_num(tc.get('tourism')):.0f} tourism/t). Sack their wonder/Great-Work cities, or flip them — a captured Theatre Square stops the bleed instantly."})
    # Religion
    tf = c["top_faith"]
    if tf and _num(tf.get("faith")) > max(c["faith"], 5) * 1.6:
        out.append({"type": "info", "weight": 52, "title": f"{pretty_leader(tf.get('leader',''))} dominates faith",
            "body": "Expect heavy missionary/Apostle pressure. Faith-buy your own Apostles with the Debater promotion to win theological combat, and station Inquisitors in your core cities."})
    # Score (overall runaway)
    tsc = c["top_score"]
    if tsc and _num(tsc.get("score")) > c["score"] * 1.5 and c["score"] > 0:
        out.append({"type": "warn", "weight": 66, "title": f"{pretty_leader(tsc.get('leader',''))} is running away with it",
            "body": f"Their score (~{int(_num(tsc.get('score')))}) towers over yours (~{int(c['score'])}). Pick their victory path and actively deny it — gang up diplomatically, or hit them militarily. Denial beats out-racing a runaway."})
    return out[:2]


def rule_victory_focus(c, strongest, chosen=False) -> list:
    key = strongest["key"]
    title, body = VICTORY_PLAYBOOK.get(key, ("Pick a victory path", "Commit to one win condition and build toward it."))
    rank_txt = f" You're #{strongest['rank']}/{strongest['total']} here." if strongest["total"] else ""
    # If the player explicitly chose this focus, push it harder (higher up the list).
    weight = 66 if chosen else 57
    return [{"type": "info", "weight": weight, "title": title, "body": body + rank_txt}]


# Phase playbook — proactive, genuinely useful guidance tuned to the era.
# Each entry is a list of weighted tips; some carry a `when` predicate so they
# only appear while they're actionable. Early eras coach the fundamentals;
# later eras offer the subtle, high-leverage plays that actually win games.

PHASE_PLAYBOOK = {
    0: [  # Ancient
        {"type": "info", "weight": 60, "title": "Slinger → Warrior → Settlers",
         "body": ("Open with a Slinger (triggers Archery eureka, costs 0 turns if you already researched it), "
                  "a Warrior for barb defence, then hard-pump Settlers. "
                  "Kill your first Barbarian with the Slinger to get Archery for free. "
                  "Districts come later — land compounds all game, buildings don't."),
         "when": lambda c: c["turn"] < 25},
        {"type": "info", "weight": 56, "title": "Magnus (Provision) → chop → Settlers",
         "body": ("Assign Magnus governor to your best production city and take the Provision promotion. "
                  "Magnus+Provision means chopped Settlers cost zero population — you keep all your citizens. "
                  "Each Forest/Rainforest/Stone chop yields ~80–140 Production depending on era. "
                  "Chop your best city's forests straight into Settlers, then use remaining charges for wonders or key districts."),
         "when": lambda c: c["cities"] < 6},
        {"type": "info", "weight": 53, "title": "Found a Pantheon early",
         "body": ("Grab a Pantheon the moment you hit 25 Faith — don't wait. "
                  "Top picks: God of the Forge (+25% Production for Ancient/Classical units, huge for warmongers), "
                  "Lady of the Reeds/Marshes (+2 Production on Marsh/Reef/Floodplains), "
                  "Divine Spark (+1 Great Person point per Holy Site, Campus, Theatre Square), "
                  "Religious Settlements (free Settler when you border a rival religion). "
                  "Free, permanent yields for the rest of the game."),
         "when": lambda c: c["faith_bal"] >= 15 and c["turn"] < 60},
    ],
    1: [  # Classical
        {"type": "info", "weight": 58, "title": "Districts before wonders — and place them right",
         "body": ("A well-placed Campus or Commercial Hub beats almost every early wonder. "
                  "Campus: next to Mountains (+1 each) or Reefs (+1 each) — 3+ adjacency is elite. "
                  "Commercial Hub: on a River (+2 base) next to other districts (+1 each). "
                  "Place the district first, then fill buildings; never build a wonder with production you could use for a district.")},
        {"type": "info", "weight": 54, "title": "Swap policy cards every civic — never leave slots weak",
         "body": ("Changing policy cards is free every time you complete a civic — there is zero Gold penalty. "
                  "Run Colonization (+50% Settler production) while expanding, "
                  "Agoge (+50% Ancient/Classical unit production) if warring, "
                  "Caravansaries (+2 Gold per Trade Route) once you have Traders. "
                  "Most players leave obsolete cards running for 20 turns — that's free yields wasted every single turn.")},
    ],
    2: [  # Medieval
        {"type": "info", "weight": 60, "title": "This is your best attack window",
         "body": ("Knights, Crossbowmen, and your unique unit peak here before everyone has Musketmen and Renaissance Walls. "
                  "To crack a city: bring 2+ Trebuchets (they hit walls AND city HP directly at full strength) "
                  "plus a Siege Tower (lets your melee attack the city's HP directly, ignoring walls) "
                  "and Crossbows for ranged fire. Without siege, melee does only 15% damage to walls. "
                  "A captured city beats 40+ turns of building."),
         "when": lambda c: c["era_idx"] == 2 and not c["losing_war"]},
        {"type": "info", "weight": 52, "title": "Chop into your most important build",
         "body": ("Builder chops are most valuable now (higher Production era modifiers). "
                  "Prioritise: a wonder you're racing for, a key district before a war, or a Settler to lock down land. "
                  "Magnus's Provision promotion still removes the population cost — always assign him before chopping Settlers.")},
    ],
    3: [  # Renaissance
        {"type": "info", "weight": 60, "title": "Lock in ONE victory path — stop hedging",
         "body": ("Your victory should be decided. Convert every spare hammer, gold, and faith into that one path. "
                  "Splitting between science AND culture AND religion means losing all three. "
                  "If you're going Science: Campuses + Rationalism card + Oxford + Spaceport pipeline. "
                  "If Culture: Theatre Squares + theming bonuses + tourism multipliers (Open Borders +25%, same Religion +25%, Trade Route +25% — all additive). "
                  "If Domination: Corps-upgraded cavalry + artillery, never stop attacking.")},
        {"type": "info", "weight": 53, "title": "Win the Great People race — patronise, don't wait",
         "body": ("Great People decide close games. "
                  "Run city projects in your Great Person cities to flood points (GP project → 100+ points of the right type). "
                  "When a key Great Person is close to spawning and a rival is competing: "
                  "spend Gold (1.5× point cost) or Faith (1× point cost) to patronise them instantly. "
                  "Never let a Great Scientist or Writer that fits your victory go to an AI.")},
    ],
    4: [  # Industrial
        {"type": "info", "weight": 58, "title": "Factory + Power Plant clusters",
         "body": ("Industrial Zones give a regional bonus to ALL cities within 6 tiles of the Factory. "
                  "Cluster 3–5 cities around one Industrial Zone, build the Factory, then Power it (Coal or Hydro). "
                  "A powered Factory gives +4 Production to every city in range — massive, quiet snowball. "
                  "Most players build one IZ per city and lose this; you want one central IZ powering several cities.")},
        {"type": "info", "weight": 52, "title": "Start the win condition this era — not next",
         "body": ("Industrial era is when the decisive infrastructure goes down. "
                  "Science win: Spaceport + begin space projects. "
                  "Culture win: Theatre Squares, Great Works theming, and start accumulating wonders for tourism. "
                  "Domination: Corps + Army upgrades for Artillery, lock in on the weakest rival capital. "
                  "Waiting until Atomic/Modern is how races get lost on the wire.")},
    ],
    5: [  # Modern
        {"type": "info", "weight": 60, "title": "Convert culture into tourism — three multipliers stack",
         "body": ("Culture doesn't win; tourism does. Each multiplier is additive, not multiplicative: "
                  "Open Borders with a rival: +25% tourism against them. "
                  "Active Trade Route to them: +25%. Same Religion: +25%. All three together: +75% bonus. "
                  "Get Open Borders with EVERY rival as soon as possible. "
                  "Then: Flight → Conservation → Computers, carpet Seaside Resorts and National Parks, run Online Communities. "
                  "Rock Bands (spam them in Industrial/Modern) can burst 20–40% of a stubborn rival's tourism threshold in one shot.")},
        {"type": "info", "weight": 52, "title": "Field a deterrent army — or someone will stop you",
         "body": ("Corps and Armies of current-era units (Infantry + Artillery, or Bombers + Fighters) are mandatory. "
                  "A visible strong army stops a rival from declaring the moment you near victory. "
                  "If you have zero military near the finish line, expect someone to race to take your capital.")},
    ],
    6: [  # Atomic
        {"type": "info", "weight": 60, "title": "Finish it — keep a deterrent",
         "body": ("Execute the win condition now: space projects, flood tourism, or final capital captures. "
                  "Keep a nuclear deterrent or a corps-strength army — on the finish line the only thing that stops you is invasion. "
                  "If another player is also close to winning, hit them militarily or diplomatically to delay; "
                  "denial beats out-racing a runaway.")},
    ],
    7: [  # Information / Future
        {"type": "info", "weight": 62, "title": "Every turn counts — finish now",
         "body": ("Final space projects, max tourism, or the last capital. "
                  "The AI is racing the same clock and can end the game before you if you waste turns. "
                  "Nothing that isn't your win condition matters.")},
    ],
}


def rule_phase_playbook(c) -> list:
    out = []
    for tip in PHASE_PLAYBOOK.get(c["era_idx"], PHASE_PLAYBOOK[0]):
        when = tip.get("when")
        if when is not None and not when(c):
            continue
        out.append({k: v for k, v in tip.items() if k != "when"})
    return out


def rule_positive(c) -> list:
    out = []
    if c["gpt"] >= 0 and not c["idle_cities"] and c["tech"].lower() not in ("none", "unknown") \
            and c["civic"].lower() not in ("none", "unknown") and not c["at_war_with"]:
        out.append({"type": "good", "weight": 28, "title": "Solid footing",
            "body": "Economy positive, queues full, no wars. Keep the tempo and push your victory path."})
    if c["cities"] >= 5 and c["turn"] < 120:
        out.append({"type": "good", "weight": 26, "title": "Strong empire size",
            "body": f"{c['cities']} cities is a healthy base. Make sure each works a Campus or Commercial Hub."})
    return out


# ── Civ-specific profiles ───────────────────────────────────────────────────
# Keyed by LEADER token. "focus" lists victory keys the civ leans toward (used
# by _strongest_path). Each tip may carry an optional "when" predicate.

def _early(c):     return c["turn"] < 60
def _veryearly(c): return c["turn"] < 35

CIV_PROFILES: dict = {
    "LEADER_AMANITORE": {"name": "Nubia — Amanitore", "focus": "science domination",
        "identity": "Builds districts faster and has the strongest early archers in the game.",
        "tips": [
            {"type": "info", "weight": 56, "when": _early, "title": "Spam districts",
             "body": "Your district discount is huge early. Prioritise Campuses and Industrial Zones — the saved production compounds."},
            {"type": "info", "weight": 52, "when": _early, "title": "Pítati Archer window",
             "body": "Your archers are cheaper and stronger. There's a real early-war timing before crossbows — use it or go wide."}]},
    "LEADER_GILGAMESH": {"name": "Sumeria — Gilgamesh", "focus": "domination science",
        "identity": "War-Carts are a brutal early rush; Ziggurats give science & culture on rivers.",
        "tips": [
            {"type": "info", "weight": 60, "when": _veryearly, "title": "War-Cart rush",
             "body": "War-Carts crush anything before spearmen. If a neighbour is close, build 3-4 and take a city while you can."},
            {"type": "info", "weight": 48, "title": "Ziggurats on rivers",
             "body": "Place Ziggurats next to rivers for free science + culture. Cheap, no district needed."}]},
    "LEADER_TRAJAN": {"name": "Rome — Trajan", "focus": "domination science culture",
        "identity": "Every city starts with a free building and free roads to the capital.",
        "tips": [
            {"type": "info", "weight": 56, "title": "Settle aggressively",
             "body": "Each new city is stronger for you (free Monument + roads). Go wide early and often."},
            {"type": "info", "weight": 46, "when": _early, "title": "Legions + ram",
             "body": "Legions with a battering ram take early cities easily — a free conquest window if a neighbour is weak."}]},
    "LEADER_VICTORIA": {"name": "England — Victoria", "focus": "domination",
        "identity": "Bonuses on other continents, strong navy (Sea Dogs), Royal Navy Dockyard.",
        "tips": [
            {"type": "info", "weight": 54, "title": "Settle other continents",
             "body": "Free melee unit + combat bonus overseas. Plan an early overseas city to unlock your power spike."}]},
    "LEADER_HARDRADA": {"name": "Norway — Harald Hardrada", "focus": "domination",
        "identity": "Coastal raiding for gold, embark from turn one, cheap naval melee.",
        "tips": [
            {"type": "info", "weight": 54, "title": "Raid the coast",
             "body": "Use Longships to pillage coastal districts for gold and healing. It funds your whole game."}]},
    "LEADER_BARBAROSSA": {"name": "Germany — Frederick Barbarossa", "focus": "domination science",
        "identity": "Extra district slot per city + the Hansa, plus bonus vs city-states.",
        "tips": [
            {"type": "info", "weight": 56, "title": "Hansa adjacency",
             "body": "You get an extra district. Cluster Hansas next to Commercial Hubs/resources for monster production."},
            {"type": "info", "weight": 44, "title": "Bully city-states",
             "body": "Combat bonus vs city-states — taking one early is a strong, low-risk power grab."}]},
    "LEADER_PERICLES": {"name": "Greece — Pericles", "focus": "culture",
        "identity": "Bonus culture per city-state you're suzerain of; the Acropolis.",
        "tips": [
            {"type": "info", "weight": 56, "title": "Court the city-states",
             "body": "Send envoys hard — each suzerainty pumps your culture. Greece wins culture through diplomacy."}]},
    "LEADER_GORGO": {"name": "Greece — Gorgo", "focus": "culture domination",
        "identity": "Culture from kills + the Hoplite/Acropolis — a warlike culture game.",
        "tips": [
            {"type": "info", "weight": 56, "title": "Fight for culture",
             "body": "Every kill gives culture. Lean into early wars with Hoplites — combat IS your culture engine."}]},
    "LEADER_GANDHI": {"name": "India — Gandhi", "focus": "religion",
        "identity": "Extra religious pressure and bonuses for staying peaceful.",
        "tips": [
            {"type": "info", "weight": 52, "title": "Stay defensive",
             "body": "Rewarded for not declaring war. Turtle, spread your religion, and tall-build."}]},
    "LEADER_CLEOPATRA": {"name": "Egypt — Cleopatra", "focus": "culture science",
        "identity": "Faster wonders/districts on floodplains, strong trade-route bonuses.",
        "tips": [
            {"type": "info", "weight": 54, "title": "Build on rivers",
             "body": "Settle floodplains for the wonder/district production bonus — Egypt is a wonder machine."},
            {"type": "info", "weight": 44, "title": "Trade everywhere",
             "body": "Your trade routes give extra gold and partners boost you too. Build Traders early."}]},
    "LEADER_QIN": {"name": "China — Qin Shi Huang", "focus": "science culture",
        "identity": "Builders can rush ancient/classical wonders; Great Wall bonuses.",
        "tips": [
            {"type": "info", "weight": 56, "when": _early, "title": "Builders into wonders",
             "body": "Use builder charges to instantly chunk out early wonders. Grab Pyramids/Great Library first."}]},
    "LEADER_MONTEZUMA": {"name": "Aztec — Montezuma", "focus": "domination",
        "identity": "Eagle Warriors capture builders; luxuries give extra amenities.",
        "tips": [
            {"type": "info", "weight": 58, "when": _early, "title": "Eagle Warrior raids",
             "body": "Eagle Warriors turn killed units into free builders. Hunt early units and barbarians relentlessly."}]},
    "LEADER_TOMYRIS": {"name": "Scythia — Tomyris", "focus": "domination",
        "identity": "Two light cavalry per build; heal on kills; bonus vs wounded.",
        "tips": [
            {"type": "info", "weight": 58, "when": _early, "title": "Saka horse swarm",
             "body": "Each light-cavalry build gives TWO units. Mass Saka Horse Archers and roll a neighbour."}]},
    "LEADER_PEDRO": {"name": "Brazil — Pedro II", "focus": "culture science",
        "identity": "Recycles spent Great Person points; jungle bonuses; Street Carnival.",
        "tips": [
            {"type": "info", "weight": 52, "title": "Chase Great People",
             "body": "You partially refund GPP. Build districts near rainforest and recruit Great People aggressively."}]},
    "LEADER_PHILIP_II": {"name": "Spain — Philip II", "focus": "religion domination",
        "identity": "Combat bonus vs other religions, cheap overseas expansion, Conquistadors.",
        "tips": [
            {"type": "info", "weight": 54, "title": "Crusade overseas",
             "body": "Found a religion, then use Conquistadors with an Apostle to fight other faiths for a big combat edge."},
            {"type": "info", "weight": 44, "title": "Colonise other continents",
             "body": "Inter-continental trade routes and fleets are cheaper — plan an overseas colony."}]},
    "LEADER_CATHERINE_DE_MEDICI": {"name": "France — Catherine de Medici", "focus": "culture",
        "identity": "Extra spies + spy bonuses, wonder culture/tourism, Chateau.",
        "tips": [
            {"type": "info", "weight": 52, "title": "Spy early, spy often",
             "body": "You get bonus spies sooner. Steal tech/gold and protect your wonders with them."}]},
    "LEADER_PETER": {"name": "Russia — Peter", "focus": "religion science culture",
        "identity": "Free territory on settling, tundra faith/production, tech/civic from trade.",
        "tips": [
            {"type": "info", "weight": 54, "title": "Settle the tundra",
             "body": "Your cities grab extra tiles and tundra works for you (Lavra faith). Settle the land nobody wants."}]},
    "LEADER_MVEMBA": {"name": "Kongo — Mvemba a Nzinga", "focus": "culture",
        "identity": "Huge bonuses from relics/artifacts + the Mbanza; no religion of its own.",
        "tips": [
            {"type": "info", "weight": 52, "title": "Hoard Great Works",
             "body": "You can't found a religion — collect relics, art, and Great People for a culture landslide."}]},
    "LEADER_SALADIN": {"name": "Arabia — Saladin", "focus": "religion science",
        "identity": "Guaranteed last religion, cheap worship buildings, science from faith.",
        "tips": [
            {"type": "info", "weight": 52, "title": "Religion = science",
             "body": "Your worship building is cheap and faith feeds science. Build Holy Sites widely and spread your faith."}]},
    "LEADER_HOJO": {"name": "Japan — Hojo Tokimune", "focus": "culture science domination",
        "identity": "Districts get bonus adjacency from each other; combat bonuses.",
        "tips": [
            {"type": "info", "weight": 56, "title": "Stack your districts",
             "body": "Districts buff each other. Pack them tightly in a few cities for compounding yields."}]},
    "LEADER_TEDDY_ROOSEVELT": {"name": "America — Teddy Roosevelt", "focus": "culture science",
        "identity": "Combat bonus on home continent, appeal/National Park bonuses, Rough Rider.",
        "tips": [
            {"type": "info", "weight": 50, "title": "Defend the homeland",
             "body": "You fight harder at home. Turtle, build wide, and chase appeal for National Parks and tourism."}]},
    "LEADER_JADWIGA": {"name": "Poland — Jadwiga", "focus": "religion culture",
        "identity": "Culture-bombs from Holy Sites/forts/encampments; relic bonuses.",
        "tips": [
            {"type": "info", "weight": 52, "title": "Culture-bomb borders",
             "body": "Encampments/Holy Sites steal neighbouring tiles. Grab resources and pressure rivals' borders."}]},
    "LEADER_JOHN_CURTIN": {"name": "Australia — John Curtin", "focus": "culture science",
        "identity": "Production surge after being attacked/liberating; coastal/appeal bonuses.",
        "tips": [
            {"type": "info", "weight": 50, "title": "Let them hit first",
             "body": "Being declared on gives a big production boost. Settle high-appeal coast and punish aggressors."}]},
    "LEADER_SEONDEOK": {"name": "Korea — Seondeok", "focus": "science",
        "identity": "Seowon is a science powerhouse; bonus science/culture from governors.",
        "tips": [
            {"type": "info", "weight": 58, "title": "Seowon on hills",
             "body": "Build Seowons early and keep their tiles clear — don't crowd them. Assign governors for the boost."}]},
    "LEADER_CHANDRAGUPTA": {"name": "India — Chandragupta", "focus": "domination",
        "identity": "Movement + combat bonus on a War of Territorial Expansion.",
        "tips": [
            {"type": "info", "weight": 54, "when": _early, "title": "Expansion war",
             "body": "Your war declaration grants speed and combat bonuses. Plan an early conquest of a close neighbour."}]},
    "LEADER_POUNDMAKER": {"name": "Cree — Poundmaker", "focus": "culture diplomacy",
        "identity": "Free Trader + trade route early; extra territory and yields from trade.",
        "tips": [
            {"type": "info", "weight": 52, "when": _early, "title": "Trade-route empire",
             "body": "You start with a free Trader. Build more — your routes grab tiles and yields nobody else gets."}]},
    "LEADER_LAUTARO": {"name": "Mapuche — Lautaro", "focus": "domination",
        "identity": "Bonus vs civs in a Golden Age; can pull population from captured cities.",
        "tips": [
            {"type": "info", "weight": 52, "title": "Punch up at the leaders",
             "body": "You fight better against civs in a Golden Age. Target the strongest, happiest empire."}]},
    "LEADER_GENGHIS_KHAN": {"name": "Mongolia — Genghis Khan", "focus": "domination",
        "identity": "Cavalry combat bonus, free promotions, visibility = combat bonus.",
        "tips": [
            {"type": "info", "weight": 58, "title": "Cavalry & Keshigs",
             "body": "Your horsemen punch far above their weight. Build the Ordu, mass cavalry, keep eyes on targets."}]},
    "LEADER_ROBERT_THE_BRUCE": {"name": "Scotland — Robert the Bruce", "focus": "science culture",
        "identity": "Happy cities give big science/production; War of Liberation.",
        "tips": [
            {"type": "info", "weight": 52, "title": "Keep cities ecstatic",
             "body": "Happy/ecstatic cities give bonus science and production. Prioritise amenities — happiness IS your economy."}]},
    "LEADER_SHAKA": {"name": "Zulu — Shaka", "focus": "domination",
        "identity": "Corps/armies form earlier and stronger; captured cities resist less.",
        "tips": [
            {"type": "info", "weight": 56, "title": "Form corps early",
             "body": "Your corps/armies come sooner and tougher. Combine units into Impi corps and steamroll."}]},
    "LEADER_WILHELMINA": {"name": "Netherlands — Wilhelmina", "focus": "culture diplomacy",
        "identity": "River/coast adjacency, polders, keeps trade yields after war.",
        "tips": [
            {"type": "info", "weight": 50, "title": "Rivers and harbours",
             "body": "Stack districts on rivers/coast and lean on trade routes — you keep their bonuses even through war."}]},
    "LEADER_CYRUS": {"name": "Persia — Cyrus", "focus": "domination",
        "identity": "Extra movement, reduced surprise-war penalties, Immortals.",
        "tips": [
            {"type": "info", "weight": 54, "title": "Surprise wars are cheap",
             "body": "You barely suffer for surprise wars and move faster after declaring. Snap-declare on an unprepared neighbour."}]},
    "LEADER_DIDO": {"name": "Phoenicia — Dido", "focus": "science culture",
        "identity": "Move your capital, faster coastal settling, Cothon, loyalty at sea.",
        "tips": [
            {"type": "info", "weight": 52, "title": "Coastal empire",
             "body": "Settle the coast aggressively — Cothons give production and your cities stay loyal across the sea."}]},
    "LEADER_GITARJA": {"name": "Indonesia — Gitarja", "focus": "religion culture",
        "identity": "Cheap coastal/faith settling, Kampung + Jong, faith-bought navy.",
        "tips": [
            {"type": "info", "weight": 50, "title": "Faith on the water",
             "body": "Settle coastal, build Holy Sites by the sea, faith-buy naval units for a religious-naval game."}]},
    "LEADER_HAMMURABI": {"name": "Babylon — Hammurabi", "focus": "science",
        "identity": "Eurekas instantly unlock the tech, but science yield is halved.",
        "tips": [
            {"type": "info", "weight": 60, "title": "Chase eurekas, not beakers",
             "body": "Your science is gutted but a completed eureka instantly grants the tech. Act to trigger eurekas — don't just bank science."}]},
    "LEADER_KRISTINA": {"name": "Sweden — Kristina", "focus": "culture science",
        "identity": "Auto-themed Great Works, bonus Great Engineers, Queen's Bibliotheque.",
        "tips": [
            {"type": "info", "weight": 50, "title": "Great Works machine",
             "body": "Buildings auto-theme Great Works for big tourism. Collect art and Great People toward a culture win."}]},
    "LEADER_LADY_SIX_SKY": {"name": "Maya — Lady Six Sky", "focus": "science",
        "identity": "Bonus yields near the capital; no fresh-water penalty; Observatory.",
        "tips": [
            {"type": "info", "weight": 56, "title": "Cluster around the capital",
             "body": "Cities within 6 tiles of your capital get bonus yields. Settle tight and build Observatories for science."}]},
    "LEADER_MANSA_MUSA": {"name": "Mali — Mansa Musa", "focus": "culture religion",
        "identity": "Enormous gold from trade/desert, reduced production. Suguba district.",
        "tips": [
            {"type": "info", "weight": 54, "title": "Buy, don't build",
             "body": "Production is low but gold is huge. Purchase buildings and units with gold/faith instead of hammering them out."}]},
    "LEADER_MATTHIAS_CORVINUS": {"name": "Hungary — Matthias Corvinus", "focus": "domination",
        "identity": "Levied city-state units are cheaper and far stronger.",
        "tips": [
            {"type": "info", "weight": 54, "title": "Levy city-state armies",
             "body": "Become suzerain, then levy their units cheaply — your bonuses make them elite. Fight with borrowed armies."}]},
    "LEADER_PACHACUTI": {"name": "Inca — Pachacuti", "focus": "science culture",
        "identity": "Mountain food/production, terrace farms, works mountain tiles.",
        "tips": [
            {"type": "info", "weight": 56, "title": "Settle the mountains",
             "body": "Mountains feed and power your cities. Build terrace farms near mountains and grow huge, dense cities."}]},
    "LEADER_WILFRID_LAURIER": {"name": "Canada — Wilfrid Laurier", "focus": "culture diplomacy",
        "identity": "Can't be surprise-warred, tundra/snow bonuses, easy emergencies.",
        "tips": [
            {"type": "info", "weight": 50, "title": "Safe and snowy",
             "body": "Nobody can surprise-war you. Settle the cold frontier freely and farm Diplomatic Favour and tourism."}]},
    "LEADER_ELEANOR_ENGLAND": {"name": "England — Eleanor", "focus": "culture",
        "identity": "Great Works drop loyalty in nearby enemy cities — flip them without fighting.",
        "tips": [
            {"type": "info", "weight": 54, "title": "Flip cities with culture",
             "body": "Pile Great Works near rival borders to crash loyalty and make their cities defect. War without armies."}]},
    "LEADER_ELEANOR_FRANCE": {"name": "France — Eleanor", "focus": "culture",
        "identity": "Great Works drop loyalty in nearby enemy cities — flip them without fighting.",
        "tips": [
            {"type": "info", "weight": 54, "title": "Flip cities with culture",
             "body": "Pile Great Works near rival borders to crash loyalty and make their cities defect. War without armies."}]},
}


def civ_tips(c) -> list:
    prof = CIV_PROFILES.get(c["leader"])
    if not prof:
        return [{"type": "info", "weight": 40, "title": "Play to your uniques",
            "body": "Check your civ's unique unit, district, and leader ability, then build toward the victory they point to."}]
    t = c["turn"]
    id_weight = 55 if t < 60 else (40 if t < 130 else 26)
    out = [{"type": "info", "weight": id_weight,
            "title": prof["name"].split("—")[0].strip() + " plan",
            "body": prof["identity"]}]
    for tip in prof.get("tips", []):
        when: Optional[Callable] = tip.get("when")
        if when is not None and not when(c):
            continue
        out.append({k: v for k, v in tip.items() if k != "when"})
    return out


# ── Board-aware rules (only fire when the full-board exporter is feeding us) ──

def _districts_of(cd):
    return [str(x).upper() for x in (cd.get("districts") or [])]

_DISTRICT_FOR_FOCUS = {"science": "CAMPUS", "culture": "THEATER",
                       "domination": "ENCAMPMENT", "religion": "HOLY_SITE",
                       "diplomacy": "CAMPUS", "auto": "CAMPUS"}
_DISTRICT_NAME = {"CAMPUS": "Campus", "THEATER": "Theatre Square",
                  "ENCAMPMENT": "Encampment", "HOLY_SITE": "Holy Site"}


def rule_district_gaps(c) -> list:
    if not c["full_board"] or c["turn"] < 20:
        return []
    dt = _DISTRICT_FOR_FOCUS.get(c.get("focus", "auto"), "CAMPUS")
    nice = _DISTRICT_NAME.get(dt, "Campus")
    missing = [cd.get("name", "a city") for cd in c["city_data"]
               if _num(cd.get("pop")) >= 4 and dt not in _districts_of(cd)]
    if not missing:
        return []
    names = ", ".join(missing[:3]) + ("…" if len(missing) > 3 else "")
    return [{"type": "info", "weight": 62, "title": f"{len(missing)} cities have no {nice}",
        "body": f"{names} lack a {nice} — that's your win engine sitting unbuilt. Drop one in each (max adjacency) before piling on buildings."}]


def rule_campus_spot(c) -> list:
    if not c["full_board"]:
        return []
    for cd in c["city_data"]:
        if _num(cd.get("campusAdj")) >= 2 and "CAMPUS" not in _districts_of(cd) and _num(cd.get("pop")) >= 3:
            return [{"type": "info", "weight": 58, "title": f"Prime Campus spot in {cd.get('name','a city')}",
                "body": f"{int(_num(cd.get('campusAdj')))} mountains sit next to {cd.get('name','it')} — a Campus there lands big science adjacency. Build it before the tiles get blocked."}]
    return []


def rule_loyalty(c) -> list:
    if not c["full_board"]:
        return []
    out = []
    for cd in c["city_data"]:
        per = _num(cd.get("loyaltyPerTurn"))
        loy = _num(cd.get("loyalty"))
        if per < 0 and loy < 100:
            turns = int(loy / -per) if per < 0 else 99
            if turns <= 15:
                out.append({"type": "warn", "weight": 90 if turns <= 6 else 72,
                    "title": f"{cd.get('name','a city')} is losing loyalty",
                    "body": f"{per:+.0f}/turn at {loy:.0f} loyalty — it flips to a free city in ~{turns} turns. "
                            "Station a Governor, build/buy a Monument or Amphitheatre, or garrison a unit now."})
    return out[:2]


_STRAT_UNIT = {"HORSES": "cavalry", "IRON": "Swordsmen/Knights", "NITER": "Musketmen/Bombards",
               "COAL": "Ironclads + Power Plants", "OIL": "Tanks/Battleships/planes",
               "ALUMINUM": "planes + spaceship parts", "URANIUM": "nukes & Giant Death Robots"}
_STRAT_FOR_ERA = {0: ["HORSES"], 1: ["IRON"], 2: ["IRON"], 3: ["NITER"],
                  4: ["COAL"], 5: ["OIL"], 6: ["ALUMINUM", "URANIUM", "OIL"],
                  7: ["ALUMINUM", "URANIUM", "OIL"]}


def rule_strategic_shortage(c) -> list:
    if not c["full_board"]:
        return []
    militaryish = (c.get("focus") == "domination" or c["at_war_with"]
                   or c["unit_summary"].get("military", 0) >= 2)
    if not militaryish:
        return []
    want = _STRAT_FOR_ERA.get(c["era_idx"], [])
    missing = [r for r in want if _num(c["strategics"].get(r, 0)) <= 0]
    if not missing:
        return []
    r = missing[0]
    return [{"type": "warn", "weight": 60, "title": f"No {r.title()} stockpiled",
        "body": f"You have 0 {r.title()} — you can't field {_STRAT_UNIT.get(r, 'key units')}. Improve a source, "
                "trade/levy for it, or take a tile that has it. Don't try to war without your era's strategic resource."}]


def rule_army_composition(c) -> list:
    if not c["full_board"]:
        return []
    us = c["unit_summary"]
    if not us:
        return []
    out = []
    warish = c["at_war_with"] or c.get("focus") == "domination"
    era = c["era_idx"]
    if warish and us.get("military", 0) >= 2 and not us.get("has_siege"):
        if era <= 1:
            body = ("No siege in your stack — melee units deal only 15% damage to walls. "
                    "Build a Catapult (full damage to walls and city HP) and pair it with a Battering Ram: "
                    "the Ram lets melee attack walls at full strength instead of 15%. "
                    "Together they break any Ancient/Classical city quickly.")
        elif era <= 3:
            body = ("No siege. Walls halve ranged damage and almost negate melee. "
                    "Trebuchets/Bombards hit both walls and city HP at full strength — bring at least two. "
                    "Add a Siege Tower: it lets melee units attack the city's HP directly, bypassing walls entirely. "
                    "Trebuchet + Siege Tower + Crossbows is the formula that cracks Medieval cities fast.")
        else:
            body = ("No siege. Modern Walls have enormous HP and make melee/cavalry useless attackers. "
                    "Artillery and Bombards deal full damage to walls and city HP directly — bring 2+. "
                    "Corps-upgraded Artillery with a ranged Army behind them ends sieges in 3–5 turns.")
        out.append({"type": "warn", "weight": 72, "title": "No siege — cities will not fall",
                    "body": body})
    if us.get("outdated", 0) >= 2:
        out.append({"type": "info", "weight": 58, "title": f"{us['outdated']} units are obsolete",
            "body": ("Several units are well below your strongest unit's Combat Strength. Upgrade them with Gold — "
                     "outdated units lose fights they should win and waste maintenance every turn. "
                     "Run the Professional Army Dark Age card (if available) or a policy card to cut upgrade costs 50%.")})
    return out


def rule_city_states(c) -> list:
    if not c["full_board"] or not c["city_states"]:
        return []
    focus = c.get("focus", "auto")
    not_mine = [s for s in c["city_states"] if not (_num(s.get("isMe")) > 0)]
    if not not_mine:
        return []  # suzerain of all visible city-states

    def _priority(s):
        name = str(s.get("name", "")).upper().replace(" ", "_").replace("-", "_")
        data = _cs_data(name)
        tier_score = {"S": 3, "A": 2, "B": 1}.get(data.get("tier", "B"), 1)
        victory_match = 1 if (focus in data.get("victory", []) or "any" in data.get("victory", [])) else 0
        return tier_score * 2 + victory_match

    target = max(not_mine, key=lambda s: (_priority(s), _num(s.get("envoys", 0))))
    cs_name  = str(target.get("name", "a city-state"))
    envoys   = int(_num(target.get("envoys", 0)))
    needed   = int(_num(target.get("needed", 0)))
    gap      = max(0, needed - envoys) if needed > 0 else "a few"
    cs_type  = str(target.get("type", "")).replace("_", " ").title() or "city-state"
    data     = _cs_data(cs_name.upper().replace(" ", "_"))
    bonus    = data.get("bonus", "a powerful unique suzerain bonus")
    tier     = data.get("tier", "")
    tier_tag = f" [Tier {tier}]" if tier else ""

    gap_txt  = (f"{gap} more envoy{'s' if gap != 1 else ''} needed"
                if isinstance(gap, int) else "a few more envoys needed")
    my_txt   = f"You have {envoys} envoy{'s' if envoys != 1 else ''} there"

    return [{"type": "info", "weight": 60,
             "title": f"Court {cs_name} ({cs_type}) — {gap_txt}",
             "body": (f"Suzerain bonus{tier_tag}: {bonus}. "
                      f"{my_txt} — send {gap_txt} to flip the suzerain. "
                      "City-state bonuses are permanent free power that most players leave on the table.")}]


def rule_trade_routes(c) -> list:
    if not c["full_board"]:
        return []
    used, cap = c["trade_used"], c["trade_cap"]
    if cap <= 0 or used >= cap:
        return []
    idle  = int(cap - used)
    era   = c["era_idx"]
    focus = c.get("focus", "auto")

    if era <= 1 and c["capped_cities"]:
        advice = ("Route domestically to housing-capped cities: domestic routes give +1 Food +1 Production "
                  "to the origin city and auto-build roads between your cities — breaking the growth stall "
                  "while connecting your empire for free.")
    elif focus == "religion":
        advice = ("Route to unconverted cities — every active Trade Route passively applies "
                  "Religious Pressure from your Holy Cities to the destination. "
                  "Pairing Traders with Missionaries accelerates conversion significantly.")
    elif focus == "culture":
        advice = ("International routes to civs with Theatre Squares stack the destination's yields onto yours. "
                  "Also: if you're suzerain of Kumasi, the origin city gains +2 Culture +1 Gold "
                  "per specialty district there — always route outward from your most district-heavy city.")
    elif focus == "science":
        advice = ("Route internationally to civs with Campuses for yield stacking. "
                  "If suzerain of Singapore, each outbound Trade Route adds +2 Production to all your cities — "
                  "route as many abroad as possible once you control Singapore.")
    else:
        advice = ("Domestic: +1 Food +1 Production to origin city (good early, builds roads). "
                  "International: more Gold and yield bonuses from destination city. "
                  "Run Triangular Trade policy card (+4 Gold +1 Faith per route) — "
                  "it's the highest raw gold yield of any econ card for most of the game.")

    return [{"type": "info", "weight": 56,
             "title": f"{idle} idle trade route{'s' if idle > 1 else ''}",
             "body": f"Every unassigned Trader is wasted yields every turn. {advice}"}]


def rule_eureka(c) -> list:
    if not c["full_board"]:
        return []
    out = []

    tech = c["tech"]
    if c["boost_tech"] == 0 and tech.lower() not in ("none", "unknown"):
        name    = _clean_name(tech)
        trigger = _eureka_trigger(tech)
        turns   = c["tech_turns"]
        urgent  = 0 < turns <= 8
        weight  = (82 if turns <= 4 else 68) if urgent else 56
        t_type  = "warn" if turns <= 4 else "info"
        prefix  = f"Only {int(turns)} turn{'s' if turns != 1 else ''} left — " if urgent else ""
        if trigger:
            body = (f"{prefix}Trigger the Eureka before this finishes: {trigger}. "
                    "A triggered Eureka removes ~40% of the research cost — "
                    "never complete a tech without it if you can avoid it.")
        else:
            body = (f"{prefix}Complete this tech's Eureka condition before it finishes. "
                    "The Eureka removes ~40% of the remaining research cost — free turns every time.")
        out.append({"type": t_type, "weight": weight,
                    "title": f"{'URGENT: ' if turns <= 4 and urgent else ''}Eureka open — {name}",
                    "body": body})

    civic = c["civic"]
    if c["boost_civic"] == 0 and civic.lower() not in ("none", "unknown"):
        name    = _clean_name(civic)
        trigger = _inspiration_trigger(civic)
        turns   = c["civic_turns"]
        urgent  = 0 < turns <= 8
        weight  = (76 if turns <= 4 else 62) if urgent else 52
        t_type  = "warn" if turns <= 4 else "info"
        prefix  = f"Only {int(turns)} turn{'s' if turns != 1 else ''} left — " if urgent else ""
        if trigger:
            body = (f"{prefix}Trigger the Inspiration: {trigger}. "
                    "Saves ~40% of the civic cost — never finish a civic without it if you can help it.")
        else:
            body = (f"{prefix}Trigger this civic's Inspiration for ~40% off. "
                    "Most players skip this every era and bleed dozens of free turns.")
        out.append({"type": t_type, "weight": weight,
                    "title": f"Inspiration open — {name}",
                    "body": body})

    return out[:2]


def rule_era_age(c) -> list:
    if not c["full_board"]:
        return []
    if c["age"] == "dark":
        era_key  = min(c["era_idx"], max(_DARK_AGE_CARDS))
        cards    = _DARK_AGE_CARDS.get(era_key, _DARK_AGE_CARDS[0])
        card_str = "; ".join(f"{n} ({s}: {e})" for n, s, e in cards[:3])
        ded_key  = min(c["era_idx"], max(_HEROIC_DEDICATIONS))
        ded_name, ded_tip = _HEROIC_DEDICATIONS.get(ded_key, _HEROIC_DEDICATIONS[0])
        return [{"type": "info", "weight": 60,
                 "title": "Dark Age — slot the strong cards, aim for Heroic",
                 "body": (f"Dark Age policies beat most normal-era cards — slot them now: {card_str}. "
                          f"Then chase Era Score hard (great wonders, world firsts, city captures, civics) — "
                          f"if you hit the Golden Age threshold this era, you get a Heroic Age next era with 3 Dedications instead of 1. "
                          f"Best Dedication to pursue: {ded_name} — {ded_tip}.")}]
    if c["age"] == "golden":
        return [{"type": "good", "weight": 42,
                 "title": "Golden Age — activate your Dedication",
                 "body": ("Your Golden Age Dedication is live — make sure it's actively exploited. "
                          "Monumentality → faith-buy Settlers, Builders, and Traders at 25% off this era. "
                          "Free Inquiry → Great Person tile improvements give +1 Science +1 Production — flood GP points. "
                          "Wish You Were Here → double tourism this era — send Rock Bands abroad. "
                          "Don't coast through a Golden Age without actively using your bonus.")}]
    return []


_STRATEGIC_RES = {"IRON", "HORSES", "NITER", "COAL", "OIL", "ALUMINUM", "URANIUM"}
_LUXURY_RES = {
    "GOLD", "SILVER", "GEMS", "MARBLE", "IVORY", "SPICES", "SUGAR", "WINE",
    "SILK", "COTTON", "DYES", "INCENSE", "FURS", "TEA", "COFFEE", "TOBACCO",
    "TRUFFLES", "AMBER", "JADE", "PEARLS", "SALT", "OLIVES", "HONEY",
    "WHALES", "TURTLES", "CINNAMON", "CLOVES", "CITRUS",
}


def rule_improve_tiles(c) -> list:
    if not c["full_board"]:
        return []
    for cd in c["city_data"]:
        un = cd.get("unimproved") or []
        if not un:
            continue
        strategic, luxury, bonus = [], [], []
        for r in un:
            key = str(r).upper().replace(" ", "_")
            if key in _STRATEGIC_RES:
                strategic.append(str(r).replace("_", " ").title())
            elif key in _LUXURY_RES:
                luxury.append(str(r).replace("_", " ").title())
            else:
                bonus.append(str(r).replace("_", " ").title())
        parts = []
        if strategic:
            parts.append(f"Strategic ({', '.join(strategic[:2])}) — required to build/upgrade this era's military units; "
                         "zero stockpile = can't field key units")
        if luxury:
            parts.append(f"Luxury ({', '.join(luxury[:2])}) — +1 Amenity to your 4 largest cities each; "
                         "each unique luxury type counts once")
        if bonus:
            parts.append(f"Bonus ({', '.join(bonus[:2])}) — extra Food/Production/Gold on the tile")
        city_name = cd.get("name", "a city")
        desc = "; ".join(parts) if parts else ", ".join(str(x).replace("_", " ").title() for x in un[:3])
        return [{"type": "info", "weight": 52,
                 "title": f"Unimproved resources at {city_name}",
                 "body": (f"Send a Builder: {desc}. "
                          "Resources only yield their bonus when improved with the correct improvement "
                          "(Mine for strategic, Plantation/Pasture/Farm for luxuries).")}]
    return []


def rule_policy_window(c) -> list:
    """Flag a high-value policy card the player is likely missing this era."""
    if not c["full_board"]:
        return []
    focus = c.get("focus", "auto")
    era   = c["era_idx"]
    # Walk highest-era first to surface the best applicable card
    candidates = [(slot, name, effect)
                  for min_era, slot, name, effect, hint in reversed(_HIGH_VALUE_CARDS)
                  if era >= min_era and hint in ("any", focus)]
    if not candidates:
        return []
    slot, name, effect = candidates[0]
    return [{"type": "info", "weight": 47,
             "title": f"Policy slot: run {name}",
             "body": (f"{name} ({slot} slot): {effect}. "
                      "Swap policy cards for free on every civic completion — "
                      "never coast on a weak card when something this strong is unlocked.")}]


BOARD_RULES = [
    rule_district_gaps, rule_campus_spot, rule_loyalty, rule_strategic_shortage,
    rule_army_composition, rule_city_states, rule_trade_routes, rule_eureka,
    rule_era_age, rule_improve_tiles, rule_policy_window,
]


GENERIC_RULES = [
    rule_economy, rule_science, rule_culture, rule_production, rule_growth,
    rule_amenities, rule_expansion, rule_military, rule_faith, rule_threats,
    rule_diplomacy, rule_war_opportunity, rule_phase_playbook, rule_positive,
] + BOARD_RULES


# ── Public entry points ─────────────────────────────────────────────────────

VICTORY_KEYS = ("science", "culture", "domination", "religion", "diplomacy")

# Which tab each rule's tips land in. Individual tips may override with their own
# "tab" key (e.g. "No research selected" is urgent → now, even though the rest of
# rule_science is plan).
RULE_TABS = {
    "rule_threats": "now", "rule_diplomacy": "now", "rule_war_opportunity": "now",
    "rule_loyalty": "now", "rule_strategic_shortage": "now",
    "rule_army_composition": "now", "rule_military": "now", "rule_economy": "now",
    "rule_production": "cities", "rule_growth": "cities", "rule_amenities": "cities",
    "rule_district_gaps": "cities", "rule_campus_spot": "cities", "rule_improve_tiles": "cities",
    "rule_science": "plan", "rule_culture": "plan", "rule_faith": "plan",
    "rule_expansion": "plan", "rule_phase_playbook": "plan", "rule_positive": "plan",
    "rule_city_states": "plan", "rule_trade_routes": "plan", "rule_eureka": "plan",
    "rule_era_age": "plan", "rule_policy_window": "plan",
}


def compute_report(state: dict, focus: str = None) -> dict:
    """Full advisor report: tips + victory tracking + headline.

    `focus` is one of VICTORY_KEYS to force a victory path, or None/'auto' to
    let the engine choose. A chosen focus steers the headline + recommendation
    while every situational warning still fires (the engine keeps its leeway).
    """
    if focus not in VICTORY_KEYS:
        focus = "auto"
    c = _context(state)
    c["focus"] = focus
    victories = _victories(c)
    strongest = _strongest_path(c, victories, focus)
    chosen = focus != "auto"

    candidates = []
    for rule in GENERIC_RULES:
        try:
            produced = rule(c) or []
        except Exception:
            continue
        tab = RULE_TABS.get(rule.__name__, "plan")
        for t in produced:
            t.setdefault("tab", tab)
        candidates.extend(produced)
    for fn, args in ((rule_rival_victory_threat, (c, victories)),
                     (rule_victory_focus, (c, strongest, chosen))):
        try:
            produced = fn(*args) or []
        except Exception:
            produced = []
        for t in produced:
            t.setdefault("tab", "plan")
        candidates.extend(produced)
    try:
        produced = civ_tips(c)
        for t in produced:
            t.setdefault("tab", "plan")
        candidates.extend(produced)
    except Exception:
        pass

    # De-dup by title, keep highest weight
    best_by_title = {}
    for tip in candidates:
        title = tip.get("title", "")
        if title not in best_by_title or tip["weight"] > best_by_title[title]["weight"]:
            best_by_title[title] = tip
    ordered = sorted(best_by_title.values(), key=lambda t: t["weight"], reverse=True)

    # Relevance floor — drop low-weight filler, but always keep a couple. We keep
    # a generous total here; the UI buckets these into Now / Plan / Cities tabs.
    strong = [t for t in ordered if t["weight"] >= MIN_SHOW]
    ranked = strong if len(strong) >= ALWAYS_SHOW else ordered[:ALWAYS_SHOW]
    ranked = ranked[:14]

    if not ranked:
        ranked = [{"type": "good", "title": "All clear", "tab": "now",
                   "body": "No problems detected. Keep building toward your victory path.", "weight": 1}]

    tips = [{"type": t["type"], "title": t["title"], "body": t["body"],
             "tab": t.get("tab", "plan")} for t in ranked]

    # Headline: the single most urgent item, or the victory recommendation.
    top = ranked[0]
    rank_txt = f" (#{strongest['rank']}/{strongest['total']})" if strongest["total"] else ""
    if top["weight"] >= 80:
        headline = top["title"]
    elif chosen:
        headline = f"Focus: {strongest['name']}{rank_txt}"
    else:
        headline = f"Best path: {strongest['name']}{rank_txt}"

    return {
        "meta": {"turn": c["turn"], "era": c["era"], "civ": c["civ"], "leader": c["leader"]},
        "headline": headline,
        "victories": victories,
        "strongest": strongest["key"],
        "focus": focus,
        "tips": tips,
    }


def compute_tips(state: dict, focus: str = None) -> list:
    """Backward-compatible: return just the tip list."""
    return compute_report(state, focus)["tips"]
