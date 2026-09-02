"""Brightness calculation helpers for Relative Light Group."""

from __future__ import annotations

import logging
from homeassistant.core import State

from .util import coerce_in

_LOGGER = logging.getLogger(__name__)

BRIGHTNESS_MAX = 255
BRIGHTNESS_MIN = 1


def brightness_values(states: list[State], attribute: str) -> list[int]:
    """Return normalized brightness values found in the given states."""
    values: list[int] = []
    for state in states:
        value = state.attributes.get(attribute)
        if value is None:
            continue
        values.append(coerce_in(int(value), BRIGHTNESS_MIN, BRIGHTNESS_MAX))
    return values


def representative_brightness(
    states: list[State],
    attribute: str,
) -> int | None:
    """Calculate the average brightness value reported by the group."""
    values = brightness_values(states, attribute)
    if not values:
        return None
    return int(sum(values) / len(values))


def base_relative_brightness_map(
    states: list[State],
    base_brightness: dict[str, int],
    target_group_brightness: int,
) -> dict[str, int]:
    """Calculate base-relative brightness targets for member lights."""
    _LOGGER.debug(
        "Starting base_relative_brightness_map: target_group_brightness=%s",
        target_group_brightness,
    )

    base_values = [
        base_brightness[state.entity_id]
        for state in states
        if state.entity_id in base_brightness
    ]
    if not base_values:
        _LOGGER.debug("No base values found, returning empty map")
        return {}

    base_group = sum(base_values) / len(base_values)
    _LOGGER.debug("Calculated base_group (average): %s from base_values: %s", base_group, base_values)
    
    if base_group <= 0:
        _LOGGER.debug("base_group <= 0, returning empty map")
        return {}

    direction = target_group_brightness - base_group
    _LOGGER.debug("Direction (target - base): %s", direction)
    brightness_map: dict[str, int] = {}

    if direction >= 0:
        max_headroom = BRIGHTNESS_MAX - base_group
        factor = direction / max_headroom if max_headroom > 0 else 0
        _LOGGER.debug("Scaling UP: max_headroom=%s, factor=%s", max_headroom, factor)
        for state in states:
            base = base_brightness.get(state.entity_id)
            if base is None:
                continue
            new_value = base + factor * (BRIGHTNESS_MAX - base)
            clamped = coerce_in(round(new_value), BRIGHTNESS_MIN, BRIGHTNESS_MAX)
            _LOGGER.debug(
                "Entity %s: base=%s -> new_value=%s (clamped to %s)",
                state.entity_id, base, new_value, clamped
            )
            brightness_map[state.entity_id] = clamped
        return brightness_map

    factor = direction / base_group
    _LOGGER.debug("Scaling DOWN: factor=%s", factor)
    for state in states:
        base = base_brightness.get(state.entity_id)
        if base is None:
            continue
        new_value = base + factor * base
        clamped = coerce_in(round(new_value), BRIGHTNESS_MIN, BRIGHTNESS_MAX)
        _LOGGER.debug(
            "Entity %s: base=%s -> new_value=%s (clamped to %s)",
            state.entity_id, base, new_value, clamped
        )
        brightness_map[state.entity_id] = clamped

    return brightness_map


def group_entity_ids_by_brightness(
    brightness_map: dict[str, int],
) -> dict[int, list[str]]:
    """Group entity_ids by their target brightness value."""
    groups: dict[int, list[str]] = {}
    for entity_id, brightness in brightness_map.items():
        groups.setdefault(brightness, []).append(entity_id)
    return groups

