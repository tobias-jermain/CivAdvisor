-- CivAdvisor_Tuner.lua  —  FireTuner exporter (no mod required)
-- =============================================================================
-- Same full-board exporter as the CivAdvisor mod, but meant to be loaded through
-- FireTuner instead of installed as a mod. This matters for MULTIPLAYER: a mod
-- enabled in the lobby must be present on every player's machine, but FireTuner
-- runs only against YOUR local client, so your friends need to install nothing.
--
-- Requirements (host/you only):
--   1. Install "Sid Meier's Civilization VI Development Tools" (free, on Steam).
--   2. In Documents\My Games\Sid Meier's Civilization VI\AppOptions.txt set:
--          EnableTuner 1
--   3. Launch Civ VI, start/join the game, then open FireTuner and connect.
--   4. In FireTuner's Lua console, load this file (File > Open Lua State... or
--      paste its contents) against the "InGame" context and run it.
--
-- It hooks the same events as the mod and also dumps once immediately, so the
-- overlay picks up your current turn right away. Output goes to Lua.log exactly
-- like the mod, so the overlay needs no changes.
--
-- Read-only + print only: multiplayer-safe, cannot affect gameplay or desync.

local function safe(fn)
    local ok, val = pcall(fn)
    if ok and val ~= nil then return val end
    return 0
end

local function safeStr(fn)
    local ok, val = pcall(fn)
    if ok and val ~= nil then return tostring(val) end
    return "unknown"
end

local function esc(s)
    s = tostring(s)
    s = s:gsub("\\", "\\\\"):gsub('"', '\\"')
    return s
end

local function strip(s, prefix)
    s = tostring(s)
    return (s:gsub("^" .. prefix, ""))
end

-- ── Production (hash lookup, the way the game's own UI does it) ──────────────
local function getProductionName(pCity)
    local result = "none"
    pcall(function()
        local bq = pCity:GetBuildQueue()
        if not bq then return end
        local hash = bq:GetCurrentProductionTypeHash()
        if hash == nil or hash == 0 then return end
        local info = GameInfo.Units[hash] or GameInfo.Buildings[hash]
                  or GameInfo.Districts[hash] or GameInfo.Projects[hash]
        if info and info.Name then result = Locale.Lookup(info.Name)
        else result = "in progress" end
    end)
    return result
end

-- ── Terrain helper: count mountains adjacent to a tile (Campus proxy) ────────
local function isMountain(p)
    if p == nil then return false end
    local ok, t = pcall(function() return p:GetTerrainType() end)
    if not ok then return false end
    local info = GameInfo.Terrains[t]
    return info ~= nil and info.Mountain == true
end

local function mountainsAround(cx, cy)
    local n = 0
    pcall(function()
        for dir = 0, 5 do
            local p = Map.GetAdjacentPlot(cx, cy, dir)
            if isMountain(p) then n = n + 1 end
        end
    end)
    return n
end

-- ── Completed districts in a city ───────────────────────────────────────────
local function cityDistricts(pCity)
    local out = {}
    pcall(function()
        for _, d in pCity:GetDistricts():Members() do
            if d:IsComplete() then
                local ok, dt = pcall(function() return GameInfo.Districts[d:GetType()].DistrictType end)
                if ok and dt then out[#out + 1] = '"' .. esc(strip(dt, "DISTRICT_")) .. '"' end
            end
        end
    end)
    return "[" .. table.concat(out, ",") .. "]"
end

-- ── Unimproved worked resources around a city (improve targets) ─────────────
local function cityUnimproved(pCity)
    local out, seen = {}, {}
    pcall(function()
        local cityPlots = Map.GetCityPlots():GetPurchasedPlots(pCity)
        for _, plotID in ipairs(cityPlots) do
            local p = Map.GetPlotByIndex(plotID)
            if p then
                local res = safe(function() return p:GetResourceType() end)
                local imp = safe(function() return p:GetImprovementType() end)
                if res ~= nil and res >= 0 and (imp == nil or imp < 0) then
                    local info = GameInfo.Resources[res]
                    if info and not seen[info.ResourceType] then
                        seen[info.ResourceType] = true
                        out[#out + 1] = '"' .. esc(strip(info.ResourceType, "RESOURCE_")) .. '"'
                    end
                end
            end
        end
    end)
    return "[" .. table.concat(out, ",") .. "]"
end

-- ── Classify a unit for the overlay (Python refines further) ────────────────
local function unitJSON(pUnit)
    local utype = safeStr(function() return GameInfo.Units[pUnit:GetType()].UnitType end)
    local combat = safe(function() return pUnit:GetCombat() end)
    local ranged = safe(function() return pUnit:GetRangedCombat() end)
    local bombard = safe(function() return pUnit:GetBombardCombat() end)
    local dmg = safe(function() return pUnit:GetDamage() end)
    local formation = safe(function() return pUnit:GetMilitaryFormation() end)
    local x = safe(function() return pUnit:GetX() end)
    local y = safe(function() return pUnit:GetY() end)
    return string.format(
        '{"type":"%s","combat":%d,"ranged":%d,"bombard":%d,"damage":%d,"formation":%d,"x":%d,"y":%d}',
        esc(strip(utype, "UNIT_")), combat, ranged, bombard, dmg, formation, x, y)
end

-- ── Main snapshot ───────────────────────────────────────────────────────────
local function serializeState(includeScan)
    local localPlayerID = Game.GetLocalPlayer()
    if localPlayerID == nil or localPlayerID < 0 then return end
    local pPlayer = Players[localPlayerID]
    if pPlayer == nil then return end

    local turnNumber = safe(function() return Game.GetCurrentGameTurn() end)
    local pCfg  = PlayerConfigurations[localPlayerID]
    local pDiplo = pPlayer:GetDiplomacy()

    print("CIV_ADVISOR_SNAP_BEGIN turn=" .. tostring(turnNumber))

    -- ===== PLAYER ============================================================
    local civName    = safeStr(function() return pCfg:GetCivilizationTypeName() end)
    local leaderName = safeStr(function() return pCfg:GetLeaderTypeName() end)
    local gold        = safe(function() return pPlayer:GetTreasury():GetGoldBalance() end)
    local gpt         = safe(function() return pPlayer:GetTreasury():GetGoldYield() - pPlayer:GetTreasury():GetTotalMaintenanceCost() end)
    local maint       = safe(function() return pPlayer:GetTreasury():GetTotalMaintenanceCost() end)
    local sci         = safe(function() return pPlayer:GetTechs():GetScienceYield() end)
    local cul         = safe(function() return pPlayer:GetCulture():GetCultureYield() end)
    local fpt         = safe(function() return pPlayer:GetReligion():GetFaithYield() end)
    local faithBal    = safe(function() return pPlayer:GetReligion():GetFaithBalance() end)
    local tourism     = safe(function() return pPlayer:GetStats():GetTourism() end)
    local score       = safe(function() return pPlayer:GetScore() end)
    local diploPts    = safe(function() return pPlayer:GetStats():GetDiplomaticVictoryPoints() end)
    local myMil       = safe(function() return pPlayer:GetStats():GetMilitaryStrength() end)
    local policies    = safe(function() return pPlayer:GetCulture():GetNumPolicies() end)

    local curTechIdx  = safe(function() return pPlayer:GetTechs():GetResearchingTech() end)
    local curTech     = safeStr(function()
        if curTechIdx and curTechIdx >= 0 then return Locale.Lookup(GameInfo.Technologies[curTechIdx].Name) end
        return "none" end)
    local techTurns   = safe(function() return pPlayer:GetTechs():GetTurnsToResearch(curTechIdx) end)
    local boostTech   = safe(function() if pPlayer:GetTechs():HasBoostBeenTriggered(curTechIdx) then return 1 else return 0 end end)
    local techsDone   = safe(function()
        local n, t = 0, pPlayer:GetTechs()
        for row in GameInfo.Technologies() do if t:HasTech(row.Index) then n = n + 1 end end
        return n end)

    local curCivicIdx = safe(function() return pPlayer:GetCulture():GetProgressingCivic() end)
    local curCivic    = safeStr(function()
        if curCivicIdx and curCivicIdx >= 0 then return Locale.Lookup(GameInfo.Civics[curCivicIdx].Name) end
        return "none" end)
    local civicTurns  = safe(function() return pPlayer:GetCulture():GetTurnsToProgressCivic(curCivicIdx) end)
    local boostCivic  = safe(function() if pPlayer:GetCulture():HasBoostBeenTriggered(curCivicIdx) then return 1 else return 0 end end)
    local civicsDone  = safe(function()
        local n, cu = 0, pPlayer:GetCulture()
        for row in GameInfo.Civics() do if cu:HasCivic(row.Index) then n = n + 1 end end
        return n end)

    local govt = safeStr(function()
        local g = pPlayer:GetCulture():GetCurrentGovernment()
        if g and g >= 0 then return strip(GameInfo.Governments[g].GovernmentType, "GOVERNMENT_") end
        return "none" end)

    local eraIndex  = safe(function() return Game.GetEras():GetCurrentEra() end)
    local eraName   = safeStr(function() return Locale.Lookup(GameInfo.Eras[Game.GetEras():GetCurrentEra()].Name) end)
    local eraScore  = safe(function() return Game.GetEras():GetPlayerCurrentScore(localPlayerID) end)
    local age = "normal"
    pcall(function()
        if Game.GetEras():HasGoldenAge(localPlayerID) then age = "golden"
        elseif Game.GetEras():HasDarkAge(localPlayerID) then age = "dark" end
    end)
    local nextGolden = safe(function() return Game.GetEras():GetPlayerThresholdForNextEra(localPlayerID) end)

    local tradeUsed = safe(function() return pPlayer:GetTrade():GetNumOutgoingRoutes() end)
    local tradeCap  = safe(function() return pPlayer:GetTrade():GetMaxOutgoingRoutes() end)

    local foundedReligion = 0
    local myRelID = safe(function() return pPlayer:GetReligion():GetReligionTypeCreated() end)
    if type(myRelID) == "number" and myRelID >= 0 then foundedReligion = 1 end

    -- strategic resource stockpiles
    local strat = {}
    pcall(function()
        local res = pPlayer:GetResources()
        for row in GameInfo.Resources() do
            if row.ResourceClassType == "RESOURCECLASS_STRATEGIC" then
                local amt = safe(function() return res:GetResourceAmount(row.Index) end)
                if amt and amt > 0 then
                    strat[#strat + 1] = string.format('"%s":%d', esc(strip(row.ResourceType, "RESOURCE_")), amt)
                end
            end
        end
    end)

    -- counts + victory signals (computed below alongside cities)
    local unitCount = safe(function()
        local n = 0; for _ in pPlayer:GetUnits():Members() do n = n + 1 end; return n end)
    local cityCount = safe(function() return pPlayer:GetCities():GetCount() end)
    local origCaps = safe(function()
        local c = 0
        for _, ct in pPlayer:GetCities():Members() do if ct:IsOriginalCapital() then c = c + 1 end end
        return c end)

    -- meta
    local speed = safeStr(function() return strip(GameInfo.GameSpeeds[GameConfiguration.GetGameSpeedType()].GameSpeedType, "GAMESPEED_") end)
    local mapsize = safeStr(function() return strip(GameInfo.Maps[GameConfiguration.GetMapSize()].MapSizeType, "MAPSIZE_") end)
    local difficulty = safeStr(function() return strip(GameInfo.Difficulties[pCfg:GetHandicapTypeID()].DifficultyType, "DIFFICULTY_") end)

    local okIDs, majorIDs = pcall(function() return PlayerManager.GetAliveMajorIDs() end)
    local totalMajors = (okIDs and majorIDs) and #majorIDs or 0

    -- world cities + religious spread
    local worldCities, myReligionCities = 0, 0
    pcall(function()
        if okIDs and majorIDs then
            for _, pid in ipairs(majorIDs) do
                local pp = Players[pid]
                if pp then
                    for _, ct in pp:GetCities():Members() do
                        worldCities = worldCities + 1
                        if foundedReligion == 1 then
                            if ct:GetReligion():GetMajorityReligion() == myRelID then
                                myReligionCities = myReligionCities + 1
                            end
                        end
                    end
                end
            end
        end
    end)

    print("CIV_ADVISOR_P " .. string.format(
        '{"turn":%d,"era":"%s","eraIndex":%d,"civ":"%s","leader":"%s","score":%d,'
        .. '"gold":%.0f,"gpt":%.1f,"maintenance":%.1f,'
        .. '"science":%.1f,"currentTech":"%s","techTurns":%d,"techsDone":%d,"boostTech":%d,'
        .. '"culture":%.1f,"currentCivic":"%s","civicTurns":%d,"civicsDone":%d,"boostCivic":%d,"tourism":%.1f,'
        .. '"faith":%.1f,"fpt":%.1f,"faithBalance":%.0f,"foundedReligion":%d,"myReligionCities":%d,"worldCities":%d,'
        .. '"cities":%d,"units":%d,"policies":%d,"myMilitary":%.0f,"government":"%s",'
        .. '"eraScore":%d,"age":"%s","nextEraThreshold":%d,"tradeUsed":%d,"tradeCap":%d,'
        .. '"origCapsHeld":%d,"totalMajors":%d,"diploPoints":%d,'
        .. '"speed":"%s","mapSize":"%s","difficulty":"%s","strategics":{%s}}',
        turnNumber, esc(eraName), eraIndex, esc(civName), esc(leaderName), score,
        gold, gpt, maint,
        sci, esc(curTech), techTurns, techsDone, boostTech,
        cul, esc(curCivic), civicTurns, civicsDone, boostCivic, tourism,
        fpt, fpt, faithBal, foundedReligion, myReligionCities, worldCities,
        cityCount, unitCount, policies, myMil, esc(govt),
        eraScore, age, nextGolden, tradeUsed, tradeCap,
        origCaps, totalMajors, diploPts,
        esc(speed), esc(mapsize), esc(difficulty), table.concat(strat, ",")))

    -- ===== CITIES ============================================================
    local ci = 0
    for _, pCity in pPlayer:GetCities():Members() do
        ci = ci + 1
        local cx = safe(function() return pCity:GetX() end)
        local cy = safe(function() return pCity:GetY() end)
        local loyalty = safe(function() return pCity:GetCulturalIdentity():GetLoyalty() end)
        local loyPer  = safe(function() return pCity:GetCulturalIdentity():GetLoyaltyPerTurn() end)
        local wall    = safe(function() return pCity:GetDistricts():GetDefenseStrength() end)

        -- nearest enemy military unit (threat)
        local nd, no, nc = 99, "", 0
        if includeScan and okIDs and majorIDs and pDiplo then
            pcall(function()
                for _, oid in ipairs(majorIDs) do
                    if oid ~= localPlayerID then
                        local okm, met = pcall(function() return pDiplo:HasMet(oid) end)
                        if okm and met then
                            for _, u in Players[oid]:GetUnits():Members() do
                                local cv = safe(function() return u:GetCombat() end)
                                if cv > 0 then
                                    local d = Map.GetPlotDistance(cx, cy, u:GetX(), u:GetY())
                                    if d <= 6 then nc = nc + 1 end
                                    if d < nd then nd = d; no = safeStr(function() return PlayerConfigurations[oid]:GetLeaderTypeName() end) end
                                end
                            end
                        end
                    end
                end
            end)
        end

        print("CIV_ADVISOR_C " .. string.format(
            '{"name":"%s","pop":%d,"food":%.1f,"prod":%.1f,"gold":%.1f,"sci":%.1f,"faith":%.1f,'
            .. '"housing":%.1f,"amenities":%d,"x":%d,"y":%d,"capital":%d,"building":"%s",'
            .. '"loyalty":%d,"loyaltyPerTurn":%d,"defense":%d,"campusAdj":%d,"districts":%s,"unimproved":%s,'
            .. '"threatDist":%d,"threatOwner":"%s","threatCount":%d}',
            esc(safeStr(function() return Locale.Lookup(pCity:GetName()) end)),
            safe(function() return pCity:GetPopulation() end),
            safe(function() return pCity:GetYield(YieldTypes.FOOD) end),
            safe(function() return pCity:GetYield(YieldTypes.PRODUCTION) end),
            safe(function() return pCity:GetYield(YieldTypes.GOLD) end),
            safe(function() return pCity:GetYield(YieldTypes.SCIENCE) end),
            safe(function() return pCity:GetYield(YieldTypes.FAITH) end),
            safe(function() return pCity:GetGrowth():GetHousing() end),
            safe(function() return pCity:GetGrowth():GetAmenities() end),
            cx, cy, safe(function() if pCity:IsCapital() then return 1 else return 0 end end),
            esc(getProductionName(pCity)),
            loyalty, loyPer, wall, mountainsAround(cx, cy),
            cityDistricts(pCity), (includeScan and cityUnimproved(pCity) or "[]"),
            nd, esc(no), nc))
        if ci >= 12 then break end
    end

    -- ===== UNITS =============================================================
    local ui = 0
    for _, pUnit in pPlayer:GetUnits():Members() do
        ui = ui + 1
        print("CIV_ADVISOR_U " .. unitJSON(pUnit))
        if ui >= 50 then break end
    end

    -- ===== RIVALS (met majors) ==============================================
    if okIDs and majorIDs and pDiplo then
        for _, oid in ipairs(majorIDs) do
            if oid ~= localPlayerID then
                local okm, met = pcall(function() return pDiplo:HasMet(oid) end)
                if okm and met then
                    local oCfg = PlayerConfigurations[oid]
                    local op = Players[oid]
                    local dstate = safeStr(function()
                        local h = pDiplo:GetDiplomaticState(oid)
                        return strip(GameInfo.DiplomaticStates[h].StateType, "DIPLO_STATE_") end)
                    local ally = safe(function() if pDiplo:HasAllianceWith(oid) then return 1 else return 0 end end)
                    print("CIV_ADVISOR_R " .. string.format(
                        '{"civ":"%s","leader":"%s","atWar":%d,"state":"%s","ally":%d,"military":%.0f,'
                        .. '"cities":%d,"science":%.1f,"culture":%.1f,"faith":%.1f,"gpt":%.1f,"score":%d,"tourism":%.1f}',
                        esc(safeStr(function() return oCfg:GetCivilizationTypeName() end)),
                        esc(safeStr(function() return oCfg:GetLeaderTypeName() end)),
                        safe(function() if pDiplo:IsAtWarWith(oid) then return 1 else return 0 end end),
                        esc(dstate), ally,
                        safe(function() return op:GetStats():GetMilitaryStrength() end),
                        safe(function() return op:GetCities():GetCount() end),
                        safe(function() return op:GetTechs():GetScienceYield() end),
                        safe(function() return op:GetCulture():GetCultureYield() end),
                        safe(function() return op:GetReligion():GetFaithYield() end),
                        safe(function() return op:GetTreasury():GetGoldYield() end),
                        safe(function() return op:GetScore() end),
                        safe(function() return op:GetStats():GetTourism() end)))
                end
            end
        end
    end

    -- ===== CITY-STATES (met minors) =========================================
    local okMinor, minorIDs = pcall(function() return PlayerManager.GetAliveMinorIDs() end)
    if okMinor and minorIDs and pDiplo then
        for _, mid in ipairs(minorIDs) do
            local okm, met = pcall(function() return pDiplo:HasMet(mid) end)
            if okm and met then
                local mp = Players[mid]
                local infl = safe(function() return mp:GetInfluence() end)
                local envoys = safe(function() return mp:GetInfluence():GetTokensReceived(localPlayerID) end)
                local suz    = safe(function() return mp:GetInfluence():GetSuzerain() end)
                local cstype = safeStr(function()
                    local lt = PlayerConfigurations[mid]:GetLeaderTypeName()
                    return strip(lt, "LEADER_MINOR_CIV_") end)
                print("CIV_ADVISOR_S " .. string.format(
                    '{"name":"%s","type":"%s","envoys":%d,"suzerain":%d,"isMe":%d}',
                    esc(safeStr(function() return Locale.Lookup(PlayerConfigurations[mid]:GetCivilizationShortDescription()) end)),
                    esc(cstype), envoys, (type(suz) == "number" and suz or -1),
                    ((type(suz) == "number" and suz == localPlayerID) and 1 or 0)))
            end
        end
    end

    print("CIV_ADVISOR_SNAP_END turn=" .. tostring(turnNumber))
end

-- ── Event hooks ─────────────────────────────────────────────────────────────
local function onLocalPlayerTurnBegin(playerID)
    serializeState(true)
end
local function onCityProductionChanged(playerID)
    if playerID == Game.GetLocalPlayer() then serializeState(false) end
end
local function onLocalPlayerTurnEnd(playerID)
    print("CIV_ADVISOR_TURNEND:1")
end

Events.LocalPlayerTurnBegin.Add(onLocalPlayerTurnBegin)
if Events.CityProductionChanged then Events.CityProductionChanged.Add(onCityProductionChanged) end
if Events.LocalPlayerTurnEnd then Events.LocalPlayerTurnEnd.Add(onLocalPlayerTurnEnd) end

-- Manual trigger you can call from the FireTuner console any time: CivAdvisorDump()
function CivAdvisorDump()
    serializeState(true)
end

-- Dump immediately on load so the overlay has data without waiting for a turn.
serializeState(true)

print("CIV_ADVISOR_LOADED: CivAdvisor FireTuner exporter active")
