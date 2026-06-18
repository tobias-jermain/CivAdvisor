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
        local id    = obj:match('"id"%s*:%s*"([^"]+)"')
        local ctype = obj:match('"type"%s*:%s*"([^"]+)"')
        local focus = obj:match('"focus"%s*:%s*"([^"]+)"') or "auto"
        if id and ctype then
            cmds[#cmds + 1] = {id = id, type = ctype, focus = focus}
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
