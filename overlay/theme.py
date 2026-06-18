"""
CivAdvisor — shared design tokens
=================================

Colours, sizes and small style constants used across the overlay, the settings
panel and the tray. Single source of truth so everything stays consistent.
"""

# Layout
CARD_W     = 326          # narrower, lighter footprint
MARGIN     = 22           # room for the drop shadow
SHADOW_R   = 30
TIPS_W     = CARD_W - 46
MAX_SCROLL = 400          # content area caps here, then scrolls
RISE       = 22           # pulse-up travel distance

# Palette
BG_TOP   = "#1A1A20"
BG_BOT   = "#0D0D11"
CARD_BG2 = "#1C1C24"
CARD_BG3 = "#23232C"
ACCENT   = "#5B8AF5"
WARM     = "#E8874A"
GOOD     = "#3ECF6C"
TEXT_HI  = "#ECECF1"
TEXT_MID = "#9A9AAB"
TEXT_LO  = "#5A5A6C"
BORDER   = "#2A2A35"
AI_ACCENT = "#A06BE8"      # distinct indigo/violet for the AI insight card

VICTORY_COLOURS = {
    "science": "#5B8AF5", "culture": "#C06BE8", "domination": "#E85A6B",
    "religion": "#E8C24A", "diplomacy": "#3ECF6C",
}
TYPE_COLOURS = {"warn": WARM, "good": GOOD, "info": ACCENT, "ai": AI_ACCENT}
TYPE_ICONS   = {"warn": "⚠", "good": "✓", "info": "→", "ai": "✦"}
