"""Constants for the Relative Light Group integration."""

from __future__ import annotations

DOMAIN = "relative_light_group"

# Easy 1-line toggles for personal preferences:
DEFAULT_REMEMBER_ON_STATE = False  # Set to True to only turn on previously active lights
DEFAULT_RESTORE_INDIVIDUAL_BRIGHTNESS = False  # Set to True to restore previous brightness levels per lamp

# Core behaviors hardcoded for optimal personal use:
DEFAULT_REMEMBER_BRIGHTNESS = True  # Preserve proportional ratios even across limits (0% or 100%)
DEFAULT_DEBOUNCE_ENABLED = True
DEFAULT_DEBOUNCE_TIME = 2000  # ms

