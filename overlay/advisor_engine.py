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
            "body": f"{', '.join(c['starving_cities'][:3])} have no surplus food and will shrink. Work farms/fishing boats or fix amenities."})
    if c["capped_cities"]:
        out.append({"type": "warn", "weight": 64, "title": "Growth stalled (housing)",
            "body": f"{', '.join(c['capped_cities'][:3])} are at the housing cap. Build a granary, aqueduct, or neighbourhoods."})
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
        {"type": "info", "weight": 60, "title": "Slinger, Warrior, then Settlers",
         "body": "Standard strong opening: a Slinger (for the Archery eureka + barb defence) and a Warrior, then hard-pump Settlers. Districts come later — claim land first.",
         "when": lambda c: c["turn"] < 25},
        {"type": "info", "weight": 56, "title": "Promote Magnus, then chop",
         "body": "Put Magnus (Provision promotion) in your best production city so chopped/bought Settlers cost no population. Then harvest forests straight into Settlers — fastest expansion in the game.",
         "when": lambda c: c["cities"] < 6},
        {"type": "info", "weight": 53, "title": "Found a Pantheon",
         "body": "Grab a Pantheon as soon as you hit 25 faith — pick for your land (God of the Forge for war, Lady of the Reeds, Divine Spark, Religious Settlements). Free permanent yields.",
         "when": lambda c: c["faith_bal"] >= 15 and c["turn"] < 60},
    ],
    1: [  # Classical
        {"type": "info", "weight": 58, "title": "Districts before wonders",
         "body": "A Campus or Commercial Hub out-values almost every early wonder. Place them on max adjacency (mountains/reefs for Campus, rivers/districts for Commercial) and only wonder-build with leftover production or chops."},
        {"type": "info", "weight": 54, "title": "Government + the right cards",
         "body": "Take a tier-1 government and run your strongest cards — Colonization (+50% Settlers) while expanding, Agoge (+50% units) if warring, Caravansaries for gold. Swap them free every civic; never leave a slot wasted."},
    ],
    2: [  # Medieval
        {"type": "info", "weight": 60, "title": "This is your attack window",
         "body": "Knights/Crossbows/your unique unit peak here, before everyone has Walls + Musketmen. If you have any military edge, declare and take a city now — domination is far cheaper this era than later.",
         "when": lambda c: c["era_idx"] == 2 and not c["losing_war"]},
        {"type": "info", "weight": 52, "title": "Chop your key builds",
         "body": "Save Builder chops and Magnus for the things that matter — a wonder, a settler, or rush-finishing an Encampment before a war. A well-timed chop skips 15+ turns of production."},
    ],
    3: [  # Renaissance
        {"type": "info", "weight": 60, "title": "Commit to ONE win — hard",
         "body": "Your path should be locked in. Stop hedging: convert every spare hammer and all your gold/faith into that one condition. Splitting between two victories is the #1 way good positions get thrown."},
        {"type": "info", "weight": 53, "title": "Win the Great People race",
         "body": "Great People decide close games. Run the project to flood points into the type you need, and patronise with gold/faith to snipe key ones (e.g. a Great Scientist or Writer) before a rival grabs them."},
    ],
    4: [  # Industrial
        {"type": "info", "weight": 58, "title": "Factory clusters = free production",
         "body": "Industrial Zones + Factories give a regional bonus. Cluster cities within 6 tiles of one IZ so several share the Factory output — then Power them. It's a massive, quiet snowball most players underbuild."},
        {"type": "info", "weight": 52, "title": "Build the win NOW, not later",
         "body": "Lay the actual win condition this era: Spaceport + start projects, mass Theatre Squares/wonders for tourism, or Corps-upgraded artillery. Leaving it to the last era is how races get lost on the wire."},
    ],
    5: [  # Modern
        {"type": "info", "weight": 60, "title": "Weaponise culture into tourism",
         "body": "Flight → Conservation → Computers, then carpet Seaside Resorts and National Parks and run Online Communities. Culture only wins once it's tourism — and Rock Bands can burst a stubborn rival open."},
        {"type": "info", "weight": 52, "title": "Field a deterrent army",
         "body": "Corps/Armies of current-era units (Infantry + Artillery, or Bombers) keep a peaceful win safe. A visible strong army stops a rival from declaring on you the moment you near victory."},
    ],
    6: [  # Atomic
        {"type": "info", "weight": 60, "title": "Close it out — and deter",
         "body": "Execute the win (space projects, tourism flood, or the last capitals) and keep a nuke or a modern army on hand. On the finish line, the only thing that beats you is being invaded — so make that impossible."},
    ],
    7: [  # Information / Future
        {"type": "info", "weight": 62, "title": "Finish — every turn counts",
         "body": "Final space projects, max tourism, or the last capital. The AIs are racing the same clock; don't waste a turn on anything that isn't your win condition."},
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
    if warish and us.get("military", 0) >= 2 and not us.get("has_siege"):
        out.append({"type": "warn", "weight": 70, "title": "No siege — you can't take cities",
            "body": "Your army has zero siege units. Melee and cavalry bounce off walls. Build or upgrade Catapults → Bombards → Artillery before you attack, or you'll just feed units to the city's ranged strike."})
    if us.get("outdated", 0) >= 2:
        out.append({"type": "info", "weight": 58, "title": f"{us['outdated']} units are obsolete",
            "body": "Several units are far below your strongest unit's strength. Upgrade them with gold — outdated units lose fights they should win and waste maintenance."})
    return out


def rule_city_states(c) -> list:
    if not c["full_board"] or not c["city_states"]:
        return []
    if any(_num(s.get("isMe")) > 0 for s in c["city_states"]):
        return []        # already suzerain of at least one
    best = max(c["city_states"], key=lambda s: _num(s.get("envoys")), default=None)
    if best is None:
        return []
    e = int(_num(best.get("envoys")))
    cstype = str(best.get("type", "")).replace("_", " ").title() or "city-state"
    return [{"type": "info", "weight": 56, "title": "You're suzerain of nobody",
        "body": f"City-state bonuses are free power. {best.get('name','A city-state')} ({cstype}) is your closest at "
                f"{e} envoy(s) — send a few more to flip its suzerain bonus and unique units to you."}]


def rule_trade_routes(c) -> list:
    if not c["full_board"]:
        return []
    used, cap = c["trade_used"], c["trade_cap"]
    if cap > 0 and used < cap:
        idle = int(cap - used)
        return [{"type": "info", "weight": 54, "title": f"{idle} idle trade route(s)",
            "body": "Every Trader is free gold (or food/production), and domestic routes build roads between your cities. Build/assign Traders to fill all your route slots."}]
    return []


def rule_eureka(c) -> list:
    if not c["full_board"]:
        return []
    if c["boost_tech"] == 0 and c["tech"].lower() not in ("none", "unknown"):
        return [{"type": "info", "weight": 50, "title": f"Eureka still open: {c['tech']}",
            "body": "Do this tech's eureka trigger (build the right thing / kill the right unit) before you finish it — it knocks roughly 40% off the cost. Free research."}]
    if c["boost_civic"] == 0 and c["civic"].lower() not in ("none", "unknown"):
        return [{"type": "info", "weight": 48, "title": f"Inspiration still open: {c['civic']}",
            "body": "Trigger this civic's inspiration before finishing it for ~40% off. Cheap progress most players skip."}]
    return []


def rule_era_age(c) -> list:
    if not c["full_board"]:
        return []
    if c["age"] == "dark":
        return [{"type": "info", "weight": 56, "title": "Dark Age — turn it into a Heroic Age",
            "body": "Dark Age policy cards are unusually strong — slot them. Then chase Era Score (firsts, wonders, taking cities) hard; a Heroic Age next era is one of the biggest swings in the game."}]
    if c["age"] == "golden":
        return [{"type": "good", "weight": 40, "title": "Golden Age — press it",
            "body": "Lean on your Golden Age dedication while it lasts (Monumentality buying with faith, Free Inquiry science, etc.). Don't coast through it."}]
    return []


def rule_improve_tiles(c) -> list:
    if not c["full_board"]:
        return []
    for cd in c["city_data"]:
        un = cd.get("unimproved") or []
        if un:
            res = ", ".join(str(x).replace("_", " ").title() for x in un[:3])
            return [{"type": "info", "weight": 50, "title": f"Unworked resources at {cd.get('name','a city')}",
                "body": f"{res} sitting unimproved — a Builder there is free yields (and luxuries are free amenities for your whole empire). Don't leave them idle."}]
    return []


BOARD_RULES = [
    rule_district_gaps, rule_campus_spot, rule_loyalty, rule_strategic_shortage,
    rule_army_composition, rule_city_states, rule_trade_routes, rule_eureka,
    rule_era_age, rule_improve_tiles,
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
    "rule_era_age": "plan",
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
