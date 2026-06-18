-- AutoAdvisor.lua  —  auto-execute companion for CivAdvisor overlay
--
-- Reads %TEMP%\civadvisor_commands.json on each turn start, executes the
-- requested auto-research / auto-civic actions, then prints results to
-- Lua.log as CIV_ADVISOR_CMD_RESULT lines for the overlay to pick up.
--
-- Runs in the InGame UI context alongside CivAdvisor.lua. Every game
-- call is pcall-guarded; a missing API or bad command is logged and skipped.
--
-- Supported commands (written by overlay/auto_controller.py):
--   auto_research  — picks the best available tech for the given focus
--   auto_civic     — picks the best available civic for the given focus

local AUTO_VERSION = 1

-- ── File path ─────────────────────────────────────────────────────────────────
local function commandsPath()
    local tmp = os.getenv("TEMP") or os.getenv("TMPDIR") or "/tmp"
    -- normalise separators
    return tmp:gsub("\\", "/") .. "/civadvisor_commands.json"
end

-- ── Simple JSON reader (only needs to handle our own flat payload) ────────────
local function readCommands()
    local path = commandsPath()
    local fh, err = io.open(path, "r")
    if not fh then return nil end
    local raw = fh:read("*a")
    fh:close()
    if not raw or #raw == 0 then return nil end

    -- Extract version and turn
    local version = tonumber(raw:match('"version"%s*:%s*(%d+)')) or 1
    local turn    = tonumber(raw:match('"turn"%s*:%s*(%d+)')) or 0

    -- Extract commands array (array of objects with "id", "type", "focus")
    local cmds = {}
    for obj in raw:gmatch("{([^{}]+)}") do
        -- skip the outer object
        local id     = obj:match('"id"%s*:%s*"([^"]+)"')
        local ctype  = obj:match('"type"%s*:%s*"([^"]+)"')
        local focus  = obj:match('"focus"%s*:%s*"([^"]+)"') or "auto"
        local combat = obj:match('"combat"%s*:%s*"?([%w]+)"?')
        if id and ctype then
            cmds[#cmds + 1] = {id = id, type = ctype, focus = focus, combat = combat}
        end
    end
    return {version = version, turn = turn, commands = cmds}
end

-- ── Tech priority tables (ordered best-first per victory path) ────────────────
local TECH_PRIORITY = {
    science = {
        "TECH_WRITING", "TECH_MATHEMATICS", "TECH_CURRENCY", "TECH_EDUCATION",
        "TECH_ASTRONOMY", "TECH_SCIENTIFIC_THEORY", "TECH_ELECTRICITY",
        "TECH_RADIO", "TECH_COMPUTERS", "TECH_NUCLEAR_FISSION", "TECH_ADVANCED_FLIGHT",
        "TECH_ROCKETRY", "TECH_NUCLEAR_FUSION", "TECH_NANOTECHNOLOGY",
        "TECH_ROBOTICS", "TECH_LASERS",
    },
    culture = {
        "TECH_WRITING", "TECH_CURRENCY", "TECH_PRINTING",
        "TECH_STEAM_POWER", "TECH_MASS_MEDIA", "TECH_COMPUTERS", "TECH_TELECOMMUNICATIONS",
    },
    domination = {
        "TECH_BRONZE_WORKING", "TECH_IRON_WORKING", "TECH_HORSEBACK_RIDING",
        "TECH_CONSTRUCTION", "TECH_MILITARY_TACTICS", "TECH_GUNPOWDER",
        "TECH_MILITARY_SCIENCE", "TECH_BALLISTICS", "TECH_STEEL",
        "TECH_RIFLING", "TECH_REPLACEABLE_PARTS", "TECH_COMBUSTION",
        "TECH_COMBINED_ARMS", "TECH_COMPOSITES", "TECH_ROBOTICS",
    },
    religion = {
        "TECH_ANIMAL_HUSBANDRY", "TECH_ARCHERY", "TECH_CURRENCY",
        "TECH_CONSTRUCTION", "TECH_CIVIL_ENGINEERING",
    },
    diplomacy = {
        "TECH_WRITING", "TECH_CURRENCY", "TECH_ASTRONOMY",
        "TECH_STEAM_POWER", "TECH_FLIGHT", "TECH_COMPUTERS",
        "TECH_TELECOMMUNICATIONS",
    },
}
-- Shared fallback for "auto" or unknown focus
TECH_PRIORITY["auto"] = TECH_PRIORITY["science"]

-- ── Civic priority tables ─────────────────────────────────────────────────────
local CIVIC_PRIORITY = {
    science = {
        "CIVIC_CRAFTSMANSHIP", "CIVIC_EARLY_EMPIRE", "CIVIC_GAMES_RECREATION",
        "CIVIC_POLITICAL_PHILOSOPHY", "CIVIC_RECORDED_HISTORY",
        "CIVIC_MEDIEVAL_FAIRES", "CIVIC_GUILDS", "CIVIC_HUMANISM",
        "CIVIC_ENLIGHTENMENT", "CIVIC_CIVIL_ENGINEERING",
        "CIVIC_SUFFRAGE", "CIVIC_TOTALITARIANISM", "CIVIC_DEMOCRACY",
    },
    culture = {
        "CIVIC_CRAFTSMANSHIP", "CIVIC_EARLY_EMPIRE", "CIVIC_DRAMA_POETRY",
        "CIVIC_THEOLOGY", "CIVIC_MEDIEVAL_FAIRES", "CIVIC_GUILDS",
        "CIVIC_HUMANISM", "CIVIC_NATURAL_HISTORY", "CIVIC_SCORCHED_EARTH",
        "CIVIC_CAPITALISM", "CIVIC_MASS_MEDIA", "CIVIC_GLOBALIZATION",
    },
    domination = {
        "CIVIC_CRAFTSMANSHIP", "CIVIC_MILITARY_TRADITION", "CIVIC_EARLY_EMPIRE",
        "CIVIC_POLITICAL_PHILOSOPHY", "CIVIC_FEUDALISM",
        "CIVIC_MERCENARIES", "CIVIC_DIPLOMATIC_LEAGUE", "CIVIC_EXPLORATION",
        "CIVIC_NATIONALISM", "CIVIC_MOBILIZATION", "CIVIC_TOTALITARIANISM",
        "CIVIC_SUFFRAGE",
    },
    religion = {
        "CIVIC_MYSTICISM", "CIVIC_STATE_WORKFORCE", "CIVIC_EARLY_EMPIRE",
        "CIVIC_FOREIGN_TRADE", "CIVIC_THEOLOGY", "CIVIC_DIVINE_RIGHT",
        "CIVIC_REFORMED_CHURCH", "CIVIC_DEMOCRATIC_CRUSADE",
    },
    diplomacy = {
        "CIVIC_CRAFTSMANSHIP", "CIVIC_FOREIGN_TRADE", "CIVIC_EARLY_EMPIRE",
        "CIVIC_POLITICAL_PHILOSOPHY", "CIVIC_DIPLOMATIC_LEAGUE",
        "CIVIC_EXPLORATION", "CIVIC_REFORMED_CHURCH",
        "CIVIC_CIVIL_ENGINEERING", "CIVIC_DEMOCRACY", "CIVIC_GLOBALIZATION",
        "CIVIC_SOCIAL_MEDIA", "CIVIC_SUFFRAGE",
    },
}
CIVIC_PRIORITY["auto"] = CIVIC_PRIORITY["science"]

-- ── Tech selection ────────────────────────────────────────────────────────────
local function pickBestTech(pPlayer, focus)
    local pTechs = pPlayer:GetTechs()
    local list   = TECH_PRIORITY[focus] or TECH_PRIORITY["auto"]

    -- Try priority list first
    for _, techType in ipairs(list) do
        local info = GameInfo.Technologies[techType]
        if info then
            local ok, canRes = pcall(function() return pTechs:CanResearch(info.Index) end)
            local ok2, has   = pcall(function() return pTechs:HasTech(info.Index) end)
            if ok and canRes and ok2 and not has then
                return info.Index, info.TechnologyType
            end
        end
    end

    -- Fallback: first available tech in any order
    for row in GameInfo.Technologies() do
        local ok, canRes = pcall(function() return pTechs:CanResearch(row.Index) end)
        local ok2, has   = pcall(function() return pTechs:HasTech(row.Index) end)
        if ok and canRes and ok2 and not has then
            return row.Index, row.TechnologyType
        end
    end
    return nil, nil
end

-- ── Civic selection ───────────────────────────────────────────────────────────
local function pickBestCivic(pPlayer, focus)
    local pCulture = pPlayer:GetCulture()
    local list     = CIVIC_PRIORITY[focus] or CIVIC_PRIORITY["auto"]

    for _, civicType in ipairs(list) do
        local info = GameInfo.Civics[civicType]
        if info then
            local ok, canProg = pcall(function() return pCulture:CanProgress(info.Index) end)
            local ok2, has    = pcall(function() return pCulture:HasCivic(info.Index) end)
            if ok and canProg and ok2 and not has then
                return info.Index, info.CivicType
            end
        end
    end

    for row in GameInfo.Civics() do
        local ok, canProg = pcall(function() return pCulture:CanProgress(row.Index) end)
        local ok2, has    = pcall(function() return pCulture:HasCivic(row.Index) end)
        if ok and canProg and ok2 and not has then
            return row.Index, row.CivicType
        end
    end
    return nil, nil
end

-- ── Setters ───────────────────────────────────────────────────────────────────
local function setResearch(pPlayer, iTech)
    -- Try direct setter first (works in many UI contexts)
    local ok = false
    pcall(function() pPlayer:GetTechs():SetResearchingTech(iTech); ok = true end)
    if not ok then
        -- Fallback: network layer (works in multiplayer-safe context)
        pcall(function() Network.SendResearchChoice(iTech); ok = true end)
    end
    return ok
end

local function setCivic(pPlayer, iCivic)
    local ok = false
    pcall(function() pPlayer:GetCulture():SetProgressingCivic(iCivic); ok = true end)
    if not ok then
        pcall(function() Network.SendCultureChoice(iCivic); ok = true end)
    end
    return ok
end

-- ════════════════════════════════════════════════════════════════════════════
--  TACTICS  —  production, policies and unit movement (incl. combat)
-- ════════════════════════════════════════════════════════════════════════════

local function plotDist(x1, y1, x2, y2)
    local d = 99
    pcall(function() d = Map.GetPlotDistance(x1, y1, x2, y2) end)
    return d
end

-- Collect at-war major+minor player IDs we've met (cached per pass).
local function hostilePlayerIDs(pPlayer, localID)
    local out = {}
    local pDiplo = pPlayer:GetDiplomacy()
    if not pDiplo then return out end
    local function scan(ids)
        if not ids then return end
        for _, oid in ipairs(ids) do
            if oid ~= localID then
                local okm, met = pcall(function() return pDiplo:HasMet(oid) end)
                local okw, war = pcall(function() return pDiplo:IsAtWarWith(oid) end)
                if okm and met and okw and war then out[#out + 1] = oid end
            end
        end
    end
    local ok1, majors = pcall(function() return PlayerManager.GetAliveMajorIDs() end)
    if ok1 then scan(majors) end
    local ok2, minors = pcall(function() return PlayerManager.GetAliveMinorIDs() end)
    if ok2 then scan(minors) end
    return out
end

-- ── Production wishlist (ordered best-first per focus) ────────────────────────
-- CanProduce() filters out anything illegal, so we throw a broad ordered list
-- and take the first item the city can actually build.
local PROD_WISHLIST = {
    science = {
        {"DISTRICT", "DISTRICT_CAMPUS"}, {"BUILDING", "BUILDING_LIBRARY"},
        {"BUILDING", "BUILDING_UNIVERSITY"}, {"BUILDING", "BUILDING_MONUMENT"},
        {"BUILDING", "BUILDING_GRANARY"}, {"UNIT", "UNIT_BUILDER"},
        {"DISTRICT", "DISTRICT_COMMERCIAL_HUB"}, {"BUILDING", "BUILDING_WATER_MILL"},
    },
    culture = {
        {"DISTRICT", "DISTRICT_THEATER"}, {"BUILDING", "BUILDING_AMPHITHEATER"},
        {"BUILDING", "BUILDING_MONUMENT"}, {"BUILDING", "BUILDING_GRANARY"},
        {"UNIT", "UNIT_BUILDER"}, {"DISTRICT", "DISTRICT_CAMPUS"},
        {"BUILDING", "BUILDING_LIBRARY"},
    },
    domination = {
        {"BUILDING", "BUILDING_WALLS"}, {"DISTRICT", "DISTRICT_ENCAMPMENT"},
        {"BUILDING", "BUILDING_BARRACKS"}, {"UNIT", "UNIT_ARCHER"},
        {"UNIT", "UNIT_SPEARMAN"}, {"UNIT", "UNIT_SWORDSMAN"},
        {"UNIT", "UNIT_WARRIOR"}, {"BUILDING", "BUILDING_MONUMENT"},
        {"UNIT", "UNIT_BUILDER"},
    },
    religion = {
        {"DISTRICT", "DISTRICT_HOLY_SITE"}, {"BUILDING", "BUILDING_SHRINE"},
        {"BUILDING", "BUILDING_TEMPLE"}, {"BUILDING", "BUILDING_MONUMENT"},
        {"BUILDING", "BUILDING_GRANARY"}, {"UNIT", "UNIT_BUILDER"},
    },
    diplomacy = {
        {"DISTRICT", "DISTRICT_COMMERCIAL_HUB"}, {"BUILDING", "BUILDING_MARKET"},
        {"DISTRICT", "DISTRICT_CAMPUS"}, {"BUILDING", "BUILDING_LIBRARY"},
        {"BUILDING", "BUILDING_MONUMENT"}, {"BUILDING", "BUILDING_GRANARY"},
        {"UNIT", "UNIT_BUILDER"},
    },
}
PROD_WISHLIST["auto"] = PROD_WISHLIST["science"]

local function prodInfo(kind, typeName)
    if kind == "UNIT"     then return GameInfo.Units[typeName] end
    if kind == "BUILDING" then return GameInfo.Buildings[typeName] end
    if kind == "DISTRICT" then return GameInfo.Districts[typeName] end
    if kind == "PROJECT"  then return GameInfo.Projects[typeName] end
    return nil
end

local function canProduce(pCity, hash)
    local result = false
    pcall(function()
        local can = pCity:GetBuildQueue():CanProduce(hash, true)
        if can then result = true end
    end)
    return result
end

local function setProduction(pCity, kind, hash)
    local ok = false
    pcall(function()
        local p = {}
        if     kind == "UNIT"     then p[CityOperationTypes.PARAM_UNIT_TYPE]     = hash
        elseif kind == "BUILDING" then p[CityOperationTypes.PARAM_BUILDING_TYPE] = hash
        elseif kind == "DISTRICT" then p[CityOperationTypes.PARAM_DISTRICT_TYPE] = hash
        elseif kind == "PROJECT"  then p[CityOperationTypes.PARAM_PROJECT_TYPE]  = hash end
        p[CityOperationTypes.PARAM_INSERT_MODE] = CityOperationTypes.VALUE_EXCLUSIVE
        CityManager.RequestOperation(pCity, CityOperationTypes.BUILD, p)
        ok = true
    end)
    return ok
end

local function cityHasQueue(pCity)
    local has = false
    pcall(function()
        local bq = pCity:GetBuildQueue()
        local hash = bq:GetCurrentProductionTypeHash()
        if hash ~= nil and hash ~= 0 then has = true end
    end)
    return has
end

-- Pick the highest-priority producible item for a city, respecting threats.
local function pickProduction(pCity, focus, underThreat)
    local list = {}
    if underThreat then
        list[#list + 1] = {"BUILDING", "BUILDING_WALLS"}
        list[#list + 1] = {"UNIT", "UNIT_ARCHER"}
        list[#list + 1] = {"UNIT", "UNIT_WARRIOR"}
    end
    for _, item in ipairs(PROD_WISHLIST[focus] or PROD_WISHLIST["auto"]) do
        list[#list + 1] = item
    end
    for _, item in ipairs(list) do
        local info = prodInfo(item[1], item[2])
        if info and info.Hash and canProduce(pCity, info.Hash) then
            return item[1], info.Hash, item[2]
        end
    end
    return nil, nil, nil
end

-- ── Production pass: fill every city with an empty queue ──────────────────────
local function runProduction(pPlayer, focus, localID)
    local set, names = 0, {}
    local hostiles = hostilePlayerIDs(pPlayer, localID)
    for _, pCity in pPlayer:GetCities():Members() do
        if not cityHasQueue(pCity) then
            local cx = safe(function() return pCity:GetX() end)
            local cy = safe(function() return pCity:GetY() end)
            local threat = false
            for _, oid in ipairs(hostiles) do
                local op = Players[oid]
                if op then
                    for _, u in op:GetUnits():Members() do
                        if safe(function() return u:GetCombat() end) > 0
                           and plotDist(cx, cy, u:GetX(), u:GetY()) <= 5 then
                            threat = true; break
                        end
                    end
                end
                if threat then break end
            end
            local kind, hash, name = pickProduction(pCity, focus, threat)
            if kind and setProduction(pCity, kind, hash) then
                set = set + 1
                if #names < 3 then names[#names + 1] = strip(name, kind .. "_") end
            end
        end
    end
    return set, names
end

-- ── Policy pass: fill empty policy slots with valid unlocked policies ─────────
local function runPolicies(pPlayer)
    local pCulture = pPlayer:GetCulture()
    local localID = Game.GetLocalPlayer()
    local numSlots = safe(function() return pCulture:GetNumPolicySlots() end)
    if numSlots == 0 then return 0, {} end

    -- Which policies are already slotted (avoid double-assign).
    local slotted = {}
    for i = 0, numSlots - 1 do
        local p = safe(function() return pCulture:GetSlotPolicy(i) end)
        if type(p) == "number" and p >= 0 then slotted[p] = true end
    end

    local adds, clears = {}, {}
    local added, names = 0, {}
    for i = 0, numSlots - 1 do
        local cur = safe(function() return pCulture:GetSlotPolicy(i) end)
        local isEmpty = not (type(cur) == "number" and cur >= 0)
        if isEmpty then
            -- find first unlocked policy that fits this slot and isn't slotted
            for row in GameInfo.Policies() do
                local idx = row.Index
                if not slotted[idx] then
                    local fits = false
                    pcall(function() fits = pCulture:CanSlotPolicy(idx, i) end)
                    if fits then
                        adds[i] = row.Hash
                        slotted[idx] = true
                        added = added + 1
                        if #names < 3 then names[#names + 1] = strip(row.PolicyType, "POLICY_") end
                        break
                    end
                end
            end
        end
    end

    if added == 0 then return 0, {} end
    local ok = false
    pcall(function()
        local p = {}
        p[PlayerOperations.PARAM_POLICY_ADD]   = adds
        p[PlayerOperations.PARAM_POLICY_CLEAR] = clears
        UI.RequestPlayerOperation(localID, PlayerOperations.CHANGE_POLICIES, p)
        ok = true
    end)
    return ok and added or 0, names
end

-- ── Unit tactics ──────────────────────────────────────────────────────────────
-- Common resource → improvement map (best-guess; CanStart validates the rest).
local RES_IMPROVEMENT = {
    RESOURCE_IRON = "IMPROVEMENT_MINE", RESOURCE_NITER = "IMPROVEMENT_MINE",
    RESOURCE_COAL = "IMPROVEMENT_MINE", RESOURCE_ALUMINUM = "IMPROVEMENT_MINE",
    RESOURCE_URANIUM = "IMPROVEMENT_MINE", RESOURCE_SILVER = "IMPROVEMENT_MINE",
    RESOURCE_GOLD = "IMPROVEMENT_MINE", RESOURCE_GEMS = "IMPROVEMENT_MINE",
    RESOURCE_COPPER = "IMPROVEMENT_MINE", RESOURCE_DIAMONDS = "IMPROVEMENT_MINE",
    RESOURCE_SALT = "IMPROVEMENT_MINE", RESOURCE_STONE = "IMPROVEMENT_QUARRY",
    RESOURCE_MARBLE = "IMPROVEMENT_QUARRY", RESOURCE_HORSES = "IMPROVEMENT_PASTURE",
    RESOURCE_CATTLE = "IMPROVEMENT_PASTURE", RESOURCE_SHEEP = "IMPROVEMENT_PASTURE",
    RESOURCE_DEER = "IMPROVEMENT_CAMP", RESOURCE_FURS = "IMPROVEMENT_CAMP",
    RESOURCE_IVORY = "IMPROVEMENT_CAMP", RESOURCE_TRUFFLES = "IMPROVEMENT_CAMP",
    RESOURCE_WHEAT = "IMPROVEMENT_FARM", RESOURCE_RICE = "IMPROVEMENT_FARM",
    RESOURCE_MAIZE = "IMPROVEMENT_FARM", RESOURCE_BANANAS = "IMPROVEMENT_PLANTATION",
    RESOURCE_CITRUS = "IMPROVEMENT_PLANTATION", RESOURCE_COCOA = "IMPROVEMENT_PLANTATION",
    RESOURCE_COFFEE = "IMPROVEMENT_PLANTATION", RESOURCE_COTTON = "IMPROVEMENT_PLANTATION",
    RESOURCE_DYES = "IMPROVEMENT_PLANTATION", RESOURCE_INCENSE = "IMPROVEMENT_PLANTATION",
    RESOURCE_SILK = "IMPROVEMENT_PLANTATION", RESOURCE_SPICES = "IMPROVEMENT_PLANTATION",
    RESOURCE_SUGAR = "IMPROVEMENT_PLANTATION", RESOURCE_TEA = "IMPROVEMENT_PLANTATION",
    RESOURCE_TOBACCO = "IMPROVEMENT_PLANTATION", RESOURCE_WINE = "IMPROVEMENT_PLANTATION",
}

local function unitFormationClass(pUnit)
    local cls = "unknown"
    pcall(function() cls = GameInfo.Units[pUnit:GetType()].FormationClass end)
    return cls or "unknown"
end

local function unitAtFullMoves(pUnit)
    local cur, max = 0, 0
    pcall(function() cur = pUnit:GetMovesRemaining() end)
    pcall(function() max = pUnit:GetMaxMoves() end)
    return max > 0 and cur >= max
end

local function tryOp(pUnit, op, params)
    local ok = false
    pcall(function()
        if UnitManager.CanStartOperation(pUnit, op, nil, params) then
            UnitManager.RequestOperation(pUnit, op, params)
            ok = true
        end
    end)
    if not ok then
        -- Some ops reject the CanStart probe form; attempt directly.
        pcall(function() UnitManager.RequestOperation(pUnit, op, params); ok = true end)
    end
    return ok
end

local function tryCommand(pUnit, cmd)
    local ok = false
    pcall(function() UnitManager.RequestCommand(pUnit, cmd); ok = true end)
    return ok
end

-- Find the nearest hostile combat unit / city within `range` of (x,y).
local function nearestHostile(hostiles, x, y, range)
    local best, bx, by, bdmg, bcity = nil, 0, 0, 0, false
    local bd = range + 1
    for _, oid in ipairs(hostiles) do
        local op = Players[oid]
        if op then
            for _, u in op:GetUnits():Members() do
                if safe(function() return u:GetCombat() end) > 0 then
                    local d = plotDist(x, y, u:GetX(), u:GetY())
                    if d < bd then
                        bd = d; best = u; bx = u:GetX(); by = u:GetY()
                        bdmg = safe(function() return u:GetDamage() end); bcity = false
                    end
                end
            end
            for _, c in op:GetCities():Members() do
                local d = plotDist(x, y, c:GetX(), c:GetY())
                if d < bd then
                    bd = d; best = c; bx = c:GetX(); by = c:GetY()
                    bdmg = 0; bcity = true
                end
            end
        end
    end
    return best, bx, by, bd, bdmg, bcity
end

-- Nearest owned unimproved resource plot for a builder to head toward.
local function nearestImproveTarget(pPlayer, ux, uy)
    local bx, by, bd, bres = nil, nil, 99, nil
    pcall(function()
        for _, pCity in pPlayer:GetCities():Members() do
            local plots = Map.GetCityPlots():GetPurchasedPlots(pCity)
            for _, plotID in ipairs(plots) do
                local p = Map.GetPlotByIndex(plotID)
                if p then
                    local res = safe(function() return p:GetResourceType() end)
                    local imp = safe(function() return p:GetImprovementType() end)
                    if res ~= nil and res >= 0 and (imp == nil or imp < 0) then
                        local d = plotDist(ux, uy, p:GetX(), p:GetY())
                        if d < bd then
                            bd = d; bx = p:GetX(); by = p:GetY()
                            local info = GameInfo.Resources[res]
                            bres = info and info.ResourceType or nil
                        end
                    end
                end
            end
        end
    end)
    return bx, by, bd, bres
end

-- One tactical action for a single unit. Returns a short verb or nil.
local function actUnit(pPlayer, pUnit, focus, combat, hostiles)
    local ux = safe(function() return pUnit:GetX() end)
    local uy = safe(function() return pUnit:GetY() end)
    local cls = unitFormationClass(pUnit)
    local promo = safeStr(function() return GameInfo.Units[pUnit:GetType()].PromotionClass end)
    local builds = safe(function() return pUnit:GetBuildCharges() end)

    -- ── Civilians ──
    if cls == "FORMATION_CLASS_CIVILIAN" then
        -- Settler: found a city if we're far enough from existing ones.
        local canFound = false
        pcall(function() canFound = GameInfo.Units[pUnit:GetType()].FoundCity == true end)
        if canFound then
            local nearOwn = 99
            for _, c in pPlayer:GetCities():Members() do
                local d = plotDist(ux, uy, c:GetX(), c:GetY())
                if d < nearOwn then nearOwn = d end
            end
            if nearOwn >= 4 and tryOp(pUnit, UnitOperationTypes.FOUND_CITY, {}) then
                return "settled"
            end
            return nil  -- otherwise leave settler placement to the player
        end
        -- Builder: improve the tile we're on, else move to the nearest target.
        if builds and builds > 0 then
            local here = Map.GetPlot(ux, uy)
            local res = safe(function() return here:GetResourceType() end)
            local imp = safe(function() return here:GetImprovementType() end)
            if res ~= nil and res >= 0 and (imp == nil or imp < 0) then
                local info = GameInfo.Resources[res]
                local impName = info and RES_IMPROVEMENT[info.ResourceType] or nil
                if impName and GameInfo.Improvements[impName] then
                    local p = {
                        [UnitOperationTypes.PARAM_X] = ux,
                        [UnitOperationTypes.PARAM_Y] = uy,
                        [UnitOperationTypes.PARAM_IMPROVEMENT_TYPE] = GameInfo.Improvements[impName].Hash,
                    }
                    if tryOp(pUnit, UnitOperationTypes.BUILD_IMPROVEMENT, p) then return "improved" end
                end
            end
            local tx, ty = nearestImproveTarget(pPlayer, ux, uy)
            if tx and tryOp(pUnit, UnitOperationTypes.MOVE_TO,
                            {[UnitOperationTypes.PARAM_X] = tx, [UnitOperationTypes.PARAM_Y] = ty}) then
                return "builder→tile"
            end
        end
        return nil
    end

    -- ── Recon: automate exploration ──
    if promo == "PROMOTION_CLASS_RECON" then
        if tryOp(pUnit, UnitOperationTypes.AUTOMATE_EXPLORE, {}) then return "exploring" end
        return nil
    end

    -- ── Military ──
    if cls == "FORMATION_CLASS_LAND_COMBAT" or cls == "FORMATION_CLASS_NAVAL"
       or cls == "FORMATION_CLASS_SUPPORT" then
        local myHP   = 100 - safe(function() return pUnit:GetDamage() end)
        local myCbt  = safe(function() return pUnit:GetCombat() end)
        local myRng  = safe(function() return pUnit:GetRangedCombat() end)
        local isRanged = myRng > myCbt

        -- Find nearest threat / target.
        local target, tx, ty, td, tdmg, tcity = nearestHostile(hostiles, ux, uy, 6)

        if combat and target then
            -- Ranged: strike anything within 2 tiles.
            if isRanged and td <= 2 then
                if tryOp(pUnit, UnitOperationTypes.RANGE_ATTACK,
                         {[UnitOperationTypes.PARAM_X] = tx, [UnitOperationTypes.PARAM_Y] = ty}) then
                    return "ranged-atk"
                end
            end
            -- Melee: engage adjacent enemy only when it's wounded or we out-stat it,
            -- and we're healthy enough to take the hit.
            if td <= 1 and myHP >= 60 then
                local worth = tcity or tdmg >= 40 or myCbt >= (safe(function() return target:GetCombat() end))
                if worth and tryOp(pUnit, UnitOperationTypes.MOVE_TO,
                                   {[UnitOperationTypes.PARAM_X] = tx, [UnitOperationTypes.PARAM_Y] = ty}) then
                    return tcity and "siege" or "melee-atk"
                end
            end
            -- Wounded: pull back and heal rather than feed the enemy.
            if myHP < 40 then
                if tryCommand(pUnit, UnitCommandTypes.FORTIFY) then return "healing" end
            end
        end

        -- No engagement → fortify in place (defensive default).
        if tryCommand(pUnit, UnitCommandTypes.FORTIFY) then return "fortified" end
        return nil
    end

    return nil
end

local function runUnits(pPlayer, focus, combat, localID)
    local acted, tally = 0, {}
    local hostiles = hostilePlayerIDs(pPlayer, localID)
    local n = 0
    for _, pUnit in pPlayer:GetUnits():Members() do
        n = n + 1
        if n > 60 then break end
        if unitAtFullMoves(pUnit) then
            local verb = nil
            pcall(function() verb = actUnit(pPlayer, pUnit, focus, combat, hostiles) end)
            if verb then
                acted = acted + 1
                tally[verb] = (tally[verb] or 0) + 1
            end
        end
    end
    local parts = {}
    for v, c in pairs(tally) do parts[#parts + 1] = c .. " " .. v end
    return acted, parts
end

-- ── Command executor ──────────────────────────────────────────────────────────
local function executeCommand(pPlayer, cmd)
    local ctype = cmd.type
    local focus = cmd.focus or "auto"

    if ctype == "auto_research" then
        -- Only set if nothing is currently queued
        local curTech = -1
        pcall(function() curTech = pPlayer:GetTechs():GetResearchingTech() end)
        if curTech ~= nil and curTech >= 0 then
            return {id = cmd.id, type = ctype, ok = false, value = "",
                    msg = "research already set"}
        end
        local iTech, techType = pickBestTech(pPlayer, focus)
        if not iTech then
            return {id = cmd.id, type = ctype, ok = false, value = "",
                    msg = "no available tech found"}
        end
        local ok = setResearch(pPlayer, iTech)
        return {id = cmd.id, type = ctype, ok = ok, value = techType or "",
                msg = ok and "ok" or "setter failed"}

    elseif ctype == "auto_civic" then
        local curCivic = -1
        pcall(function() curCivic = pPlayer:GetCulture():GetProgressingCivic() end)
        if curCivic ~= nil and curCivic >= 0 then
            return {id = cmd.id, type = ctype, ok = false, value = "",
                    msg = "civic already set"}
        end
        local iCivic, civicType = pickBestCivic(pPlayer, focus)
        if not iCivic then
            return {id = cmd.id, type = ctype, ok = false, value = "",
                    msg = "no available civic found"}
        end
        local ok = setCivic(pPlayer, iCivic)
        return {id = cmd.id, type = ctype, ok = ok, value = civicType or "",
                msg = ok and "ok" or "setter failed"}

    elseif ctype == "auto_production" then
        local localID = Game.GetLocalPlayer()
        local set, names = runProduction(pPlayer, focus, localID)
        return {id = cmd.id, type = ctype, ok = set > 0,
                value = set .. " cities" .. (#names > 0 and (": " .. table.concat(names, ", ")) or ""),
                msg = set > 0 and "ok" or "nothing to queue"}

    elseif ctype == "auto_policy" then
        local added, names = runPolicies(pPlayer)
        return {id = cmd.id, type = ctype, ok = added > 0,
                value = added .. " slots" .. (#names > 0 and (": " .. table.concat(names, ", ")) or ""),
                msg = added > 0 and "ok" or "no empty slots"}

    elseif ctype == "auto_units" then
        local localID = Game.GetLocalPlayer()
        local combat = cmd.combat == "1" or cmd.combat == "true"
        local acted, parts = runUnits(pPlayer, focus, combat, localID)
        return {id = cmd.id, type = ctype, ok = acted > 0,
                value = acted .. " units" .. (#parts > 0 and (" (" .. table.concat(parts, ", ") .. ")") or ""),
                msg = acted > 0 and "ok" or "no units acted"}
    end

    return {id = cmd.id, type = ctype, ok = false, value = "", msg = "unknown command type"}
end

-- ── Result serialiser (minimal JSON, no library needed) ───────────────────────
local function resultJSON(r)
    local okStr  = r.ok  and "true" or "false"
    local val    = tostring(r.value or ""):gsub("\\", "\\\\"):gsub('"', '\\"')
    local msg    = tostring(r.msg   or ""):gsub("\\", "\\\\"):gsub('"', '\\"')
    local id     = tostring(r.id    or ""):gsub("\\", "\\\\"):gsub('"', '\\"')
    local typ    = tostring(r.type  or ""):gsub("\\", "\\\\"):gsub('"', '\\"')
    return string.format('{"id":"%s","type":"%s","ok":%s,"value":"%s","msg":"%s"}',
                         id, typ, okStr, val, msg)
end

-- ── Main handler ──────────────────────────────────────────────────────────────
local function onLocalPlayerTurnBegin(playerID)
    if playerID ~= Game.GetLocalPlayer() then return end
    local pPlayer = Players[playerID]
    if not pPlayer then return end

    local payload = readCommands()
    if not payload or not payload.commands or #payload.commands == 0 then return end

    for _, cmd in ipairs(payload.commands) do
        local result = {id = cmd.id, type = cmd.type, ok = false, value = "", msg = "error"}
        pcall(function() result = executeCommand(pPlayer, cmd) end)
        print("CIV_ADVISOR_CMD_RESULT " .. resultJSON(result))
    end

    -- Delete the commands file so stale commands don't fire next turn
    pcall(function() os.remove(commandsPath()) end)
end

Events.LocalPlayerTurnBegin.Add(onLocalPlayerTurnBegin)
print("CIV_ADVISOR_AUTO: AutoAdvisor loaded (v" .. AUTO_VERSION .. ")")
