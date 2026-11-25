"""Platform allowing several lights to be grouped into one with relative brightness control."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import itertools
import logging
import time
from typing import Any, cast

from homeassistant.components import light
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_FLASH,
    ATTR_HS_COLOR,
    ATTR_MAX_COLOR_TEMP_KELVIN,
    ATTR_MIN_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_RGBWW_COLOR,
    ATTR_SUPPORTED_COLOR_MODES,
    ATTR_TRANSITION,
    ATTR_WHITE,
    ATTR_XY_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
    filter_supported_color_modes,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ASSUMED_STATE,
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    CONF_ENTITIES,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .brightness import (
    base_relative_brightness_map,
    group_entity_ids_by_brightness,
    representative_brightness,
)
from .const import DEFAULT_DEBOUNCE_TIME
from .entity import GroupEntity
from .util import (
    find_state_attributes,
    mean_circle,
    mean_tuple,
    reduce_attribute,
)

PARALLEL_UPDATES = 0

GROUP_CONTEXT_WINDOW = 10

SUPPORT_GROUP_LIGHT = (
    LightEntityFeature.EFFECT
    | LightEntityFeature.FLASH
    | LightEntityFeature.TRANSITION
)

_LOGGER = logging.getLogger(__name__)

FORWARDED_ATTRIBUTES = frozenset(
    {
        ATTR_BRIGHTNESS,
        ATTR_COLOR_TEMP_KELVIN,
        ATTR_EFFECT,
        ATTR_FLASH,
        ATTR_HS_COLOR,
        ATTR_RGB_COLOR,
        ATTR_RGBW_COLOR,
        ATTR_RGBWW_COLOR,
        ATTR_TRANSITION,
        ATTR_WHITE,
        ATTR_XY_COLOR,
    }
)

# Attributes that represent visual changes (not brightness or on/off control)
VISUAL_ATTRIBUTES = frozenset(
    {
        ATTR_COLOR_TEMP_KELVIN,
        ATTR_EFFECT,
        ATTR_FLASH,
        ATTR_HS_COLOR,
        ATTR_RGB_COLOR,
        ATTR_RGBW_COLOR,
        ATTR_RGBWW_COLOR,
        ATTR_WHITE,
        ATTR_XY_COLOR,
    }
)

VALID_MEMBER_STATES = frozenset({STATE_ON, STATE_OFF})


@dataclass(slots=True)
class MemberStateSnapshot:
    """Resolved view of the group's members for state-based decisions."""

    enabled_entity_ids: list[str]
    disabled_entity_ids: list[str]
    missing_entity_ids: list[str]
    unavailable_entity_ids: list[str]
    unknown_entity_ids: list[str]
    off_entity_ids: list[str]
    on_entity_ids: list[str]
    enabled_states: list[State]
    valid_states: list[State]
    on_states: list[State]
    valid_states_by_id: dict[str, State]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Initialize Relative Light Group config entry."""
    registry = er.async_get(hass)
    entities = er.async_validate_entity_ids(
        registry, config_entry.options[CONF_ENTITIES]
    )

    async_add_entities(
        [
            RelativeLightGroup(
                config_entry.entry_id,
                config_entry.title,
                entities,
            )
        ]
    )


@callback
def async_create_preview_light(
    hass: HomeAssistant, name: str, validated_config: dict[str, Any]
) -> RelativeLightGroup:
    """Create a preview light entity."""
    return RelativeLightGroup(
        None,
        name,
        validated_config[CONF_ENTITIES],
    )


class RelativeLightGroup(GroupEntity, LightEntity):
    """Representation of a relative light group.

    Brightness changes are distributed proportionally among on lights.
    Color/effect changes are forwarded only to on lights.
    Turn on/off affects all lights.
    The brightness ratio is kept at the limits and replaced when a member is
    changed outside the group.
    """

    _attr_available = False
    _attr_max_color_temp_kelvin = 6500
    _attr_min_color_temp_kelvin = 2000
    _attr_should_poll = False
    _attr_translation_key = "relative_light_group"

    def __init__(
        self,
        unique_id: str | None,
        name: str,
        entity_ids: list[str],
    ) -> None:
        """Initialize a relative light group."""
        self._entity_ids = entity_ids
        self._attr_name = name
        self._attr_extra_state_attributes = {ATTR_ENTITY_ID: entity_ids}
        self._attr_unique_id = unique_id
        self._base_brightness: dict[str, int] = {}
        self._last_command_contexts: dict[str, float] = {}

        self._debounce_time = DEFAULT_DEBOUNCE_TIME
        self._last_command_time = 0.0
        self._last_command_debounce_eligible = False
        self._debounce_sync_unsub: CALLBACK_TYPE | None = None

        self._attr_color_mode = ColorMode.UNKNOWN
        self._attr_supported_color_modes = {ColorMode.ONOFF}

    async def async_added_to_hass(self) -> None:
        """Register listeners and cancel debounce sync on remove."""
        await super().async_added_to_hass()

        def _cancel_debounce_sync() -> None:
            self._async_cancel_debounce_sync()

        self.async_on_remove(_cancel_debounce_sync)

    @callback
    def _async_cancel_debounce_sync(self) -> None:
        """Cancel a pending post-debounce member sync."""
        if self._debounce_sync_unsub is not None:
            self._debounce_sync_unsub()
            self._debounce_sync_unsub = None

    @callback
    def _update_assumed_state_from_members(self) -> None:
        """Update assumed_state based only on enabled members."""
        self._attr_assumed_state = False
        for entity_id in self._get_enabled_entity_ids():
            if (state := self.hass.states.get(entity_id)) is None:
                continue
            if state.attributes.get(ATTR_ASSUMED_STATE):
                self._attr_assumed_state = True
                return

    @callback
    def _is_member_enabled(self, entity_id: str) -> bool:
        """Return True when the member is not disabled in the entity registry."""
        registry_entry = er.async_get(self.hass).async_get(entity_id)
        return registry_entry is None or registry_entry.disabled_by is None

    @callback
    def _get_enabled_entity_ids(self) -> list[str]:
        """Return configured member IDs excluding disabled entities."""
        return [
            entity_id
            for entity_id in self._entity_ids
            if self._is_member_enabled(entity_id)
        ]

    @callback
    def _get_member_snapshot(self) -> MemberStateSnapshot:
        """Collect enabled members and classify which ones have a valid light state."""
        registry = er.async_get(self.hass)
        enabled_entity_ids: list[str] = []
        disabled_entity_ids: list[str] = []
        missing_entity_ids: list[str] = []
        unavailable_entity_ids: list[str] = []
        unknown_entity_ids: list[str] = []
        off_entity_ids: list[str] = []
        on_entity_ids: list[str] = []
        enabled_states: list[State] = []
        valid_states: list[State] = []
        on_states: list[State] = []
        valid_states_by_id: dict[str, State] = {}

        for entity_id in self._entity_ids:
            registry_entry = registry.async_get(entity_id)
            state = self.hass.states.get(entity_id)

            if registry_entry is not None and registry_entry.disabled_by is not None:
                disabled_entity_ids.append(entity_id)
                continue

            enabled_entity_ids.append(entity_id)

            if state is None:
                if registry_entry is None:
                    missing_entity_ids.append(entity_id)
                continue

            enabled_states.append(state)

            if state.state == STATE_UNAVAILABLE:
                unavailable_entity_ids.append(entity_id)
            elif state.state == STATE_UNKNOWN:
                unknown_entity_ids.append(entity_id)
            elif state.state == STATE_OFF:
                off_entity_ids.append(entity_id)
            elif state.state == STATE_ON:
                on_entity_ids.append(entity_id)

            if state.state not in VALID_MEMBER_STATES:
                continue

            valid_states.append(state)
            valid_states_by_id[state.entity_id] = state
            if state.state == STATE_ON:
                on_states.append(state)

        return MemberStateSnapshot(
            enabled_entity_ids=enabled_entity_ids,
            disabled_entity_ids=disabled_entity_ids,
            missing_entity_ids=missing_entity_ids,
            unavailable_entity_ids=unavailable_entity_ids,
            unknown_entity_ids=unknown_entity_ids,
            off_entity_ids=off_entity_ids,
            on_entity_ids=on_entity_ids,
            enabled_states=enabled_states,
            valid_states=valid_states,
            on_states=on_states,
            valid_states_by_id=valid_states_by_id,
        )

    def _state_supports_brightness(self, state: Any) -> bool | None:
        """Infer whether a member should contribute to group brightness."""
        if state is None or state.state not in VALID_MEMBER_STATES:
            return None

        if state.attributes.get(ATTR_BRIGHTNESS) is not None:
            return True

        supported_modes = state.attributes.get(ATTR_SUPPORTED_COLOR_MODES)
        if supported_modes is not None:
            return any(mode != ColorMode.ONOFF for mode in supported_modes)

        color_mode = state.attributes.get(ATTR_COLOR_MODE)
        if color_mode is not None:
            return color_mode != ColorMode.ONOFF

        return None

    def _is_turn_on_optimistic(self, data: dict[str, Any], was_off: bool) -> bool:
        """Return True when turn_on can safely publish an optimistic state."""
        snapshot = self._get_member_snapshot()
        if not snapshot.valid_states:
            return False

        if was_off and ATTR_BRIGHTNESS not in data:
            return not any(
                self._state_supports_brightness(snapshot.valid_states_by_id.get(eid))
                for eid in snapshot.enabled_entity_ids
            )

        return True

    def _prepare_command_execution(self, optimistic: bool) -> None:
        """Prime debounce bookkeeping and record the command context."""
        self._async_cancel_debounce_sync()
        now = time.monotonic()
        self._last_command_contexts = {
            context_id: recorded_at
            for context_id, recorded_at in self._last_command_contexts.items()
            if now - recorded_at < GROUP_CONTEXT_WINDOW
        }
        if self._context:
            self._last_command_contexts[self._context.id] = now
        self._last_command_debounce_eligible = optimistic
        self._last_command_time = now

    @callback
    def _async_sync_group_state(self) -> None:
        """Refresh derived state and write it if the update succeeded."""
        if not self.hass.is_running:
            return
        self.async_update_group_state()
        self.async_write_ha_state()

    @callback
    def _async_write_optimistic_state(self) -> None:
        """Publish the optimistic group state immediately."""
        if self.hass.is_running:
            self.async_write_ha_state()

    @callback
    def _async_schedule_post_command_sync(self) -> None:
        """Sync once after the current command has had time to settle."""
        if self._debounce_sync_unsub is not None:
            return

        remaining = self._debounce_time / 1000 - (
            time.monotonic() - self._last_command_time
        )
        if remaining <= 0:
            self._async_sync_group_state()
            return

        @callback
        def _sync(_dt: datetime) -> None:
            self._debounce_sync_unsub = None
            self._async_sync_group_state()

        self._debounce_sync_unsub = async_call_later(self.hass, remaining, _sync)

    def _context_belongs_to_group_command(self, context: Context | None) -> bool:
        """Return True if the context was produced by a recent group command."""
        if context is None:
            return False
        now = time.monotonic()
        return any(
            (recorded_at := self._last_command_contexts.get(context_id)) is not None
            and now - recorded_at < GROUP_CONTEXT_WINDOW
            for context_id in (context.id, context.parent_id)
        )

    def _event_context_belongs_to_group_command(
        self, event: Event[EventStateChangedData]
    ) -> bool:
        """Return True if the member event came from this group."""
        new_state = event.data.get("new_state")
        if new_state is not None and new_state.context is not None:
            return self._context_belongs_to_group_command(new_state.context)
        return self._context_belongs_to_group_command(event.context)

    @callback
    def async_member_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Take the current levels as the new ratio after an outside change."""
        if self._event_context_belongs_to_group_command(event):
            return

        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        if (
            old_state is None
            or new_state is None
            or old_state.state not in VALID_MEMBER_STATES
            or new_state.state not in VALID_MEMBER_STATES
        ):
            return

        if (old_state.state, old_state.attributes.get(ATTR_BRIGHTNESS)) == (
            new_state.state,
            new_state.attributes.get(ATTR_BRIGHTNESS),
        ):
            return

        self._base_brightness = {
            state.entity_id: int(brightness)
            for state in self._get_on_lights()
            if (brightness := state.attributes.get(ATTR_BRIGHTNESS)) is not None
        }

    @callback
    def async_should_defer_state_change(
        self,
        event: Event[EventStateChangedData],
    ) -> bool:
        """Ignore only group-originated member updates during debounce."""
        if not self._last_command_debounce_eligible:
            return False

        if time.monotonic() - self._last_command_time >= self._debounce_time / 1000:
            return False

        if not self._event_context_belongs_to_group_command(event):
            return False

        self._async_schedule_post_command_sync()
        return True

    def _get_on_lights(self) -> list:
        """Get list of currently on light states."""
        return self._get_member_snapshot().on_states

    def _ensure_base_brightness(self, on_lights: list) -> None:
        """Ensure base brightness is captured for all on lights."""
        for state in on_lights:
            eid = state.entity_id
            if eid not in self._base_brightness:
                brightness = state.attributes.get(ATTR_BRIGHTNESS)
                if brightness is not None:
                    self._base_brightness[eid] = int(brightness)

    async def _async_call_light_service(
        self,
        service: str,
        call_data: dict[str, Any],
    ) -> HomeAssistantError | None:
        """Call a light service and return the service error, if any."""
        try:
            await self.hass.services.async_call(
                light.DOMAIN,
                service,
                call_data,
                blocking=True,
                context=self._context,
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "Light service %s failed for %s: %s",
                service,
                call_data.get(ATTR_ENTITY_ID),
                err,
            )
            return err
        return None

    async def _apply_brightness_with_base(
        self,
        data: dict[str, Any],
        on_lights: list,
        target_brightness: int,
    ) -> None:
        """Apply brightness using base-relative algorithm.

        When going UP from base: uses relative headroom distribution
        (all lights reach max together). When going DOWN: scales
        proportionally from base (preserving ratios perfectly).
        Always references base brightness, so ratios are never lost
        even after hitting limits.
        """
        self._ensure_base_brightness(on_lights)
        brightness_map = base_relative_brightness_map(
            on_lights, self._base_brightness, target_brightness
        )

        if not brightness_map:
            return

        visual_data = {
            key: value for key, value in data.items() if key in VISUAL_ATTRIBUTES
        }

        batches = []
        for br, eids in group_entity_ids_by_brightness(brightness_map).items():
            call_data = {**visual_data}
            call_data[ATTR_BRIGHTNESS] = br
            call_data[ATTR_ENTITY_ID] = eids
            if ATTR_TRANSITION in data:
                call_data[ATTR_TRANSITION] = data[ATTR_TRANSITION]
            batches.append(call_data)

        results = await asyncio.gather(
            *(self._async_call_light_service(SERVICE_TURN_ON, batch) for batch in batches)
        )
        if all(results):
            raise results[0]

    async def _async_turn_on_targets_from_group_off(
        self, data: dict[str, Any], target_entity_ids: list[str]
    ) -> None:
        """Turn on all targets after the group was off."""
        if not target_entity_ids:
            return

        data[ATTR_ENTITY_ID] = target_entity_ids
        if err := await self._async_call_light_service(SERVICE_TURN_ON, data):
            raise err

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Forward the turn_on command with relative brightness control.

        Behavior:
        - If the group is OFF (being turned on): turn on all enabled lights.
          An explicit brightness sets every light to it and replaces the ratio.
        - If brightness is changing: scale on lights from the remembered ratio.
        - If color/effect is changing: only apply to on lights.
        """
        data = {
            key: value for key, value in kwargs.items() if key in FORWARDED_ATTRIBUTES
        }

        was_off = not self._attr_is_on
        optimistic = self._is_turn_on_optimistic(data, was_off)
        self._prepare_command_execution(optimistic)

        if optimistic:
            self._attr_is_on = True
            if (brightness := data.get(ATTR_BRIGHTNESS)) is not None:
                self._attr_brightness = brightness
            if ATTR_HS_COLOR in data:
                self._attr_hs_color = data[ATTR_HS_COLOR]
            if ATTR_RGB_COLOR in data:
                self._attr_rgb_color = data[ATTR_RGB_COLOR]
            if ATTR_COLOR_TEMP_KELVIN in data:
                self._attr_color_temp_kelvin = data[ATTR_COLOR_TEMP_KELVIN]
            self._async_write_optimistic_state()

        on_lights = self._get_on_lights()
        has_brightness = ATTR_BRIGHTNESS in data
        has_visual_attrs = any(key in data for key in VISUAL_ATTRIBUTES)

        try:
            # Case 1: Group was OFF → turning on
            if was_off:
                if has_brightness:
                    self._base_brightness.clear()
                await self._async_turn_on_targets_from_group_off(
                    data, self._get_enabled_entity_ids()
                )
                return

            # Case 2: Group is ON and brightness is being changed
            if has_brightness and on_lights:
                await self._apply_brightness_with_base(
                    data, on_lights, data[ATTR_BRIGHTNESS]
                )
                return

            # Case 3: Group is ON, no brightness change, but visual attributes
            if has_visual_attrs and on_lights:
                on_entity_ids = [state.entity_id for state in on_lights]
                visual_data = {
                    key: value
                    for key, value in data.items()
                    if key in VISUAL_ATTRIBUTES or key == ATTR_TRANSITION
                }
                visual_data[ATTR_ENTITY_ID] = on_entity_ids

                if err := await self._async_call_light_service(
                    SERVICE_TURN_ON, visual_data
                ):
                    raise err
                return

            # Case 4: Fallback – no special handling needed
            data[ATTR_ENTITY_ID] = self._get_enabled_entity_ids()
            if not data[ATTR_ENTITY_ID]:
                return

            if err := await self._async_call_light_service(SERVICE_TURN_ON, data):
                raise err
        finally:
            if optimistic:
                self._async_schedule_post_command_sync()
            else:
                self._async_sync_group_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Forward the turn_off command to all lights in the light group."""
        snapshot = self._get_member_snapshot()
        optimistic = bool(snapshot.valid_states)
        self._prepare_command_execution(optimistic)
        if optimistic:
            self._attr_is_on = False
            self._async_write_optimistic_state()

        data = {ATTR_ENTITY_ID: snapshot.enabled_entity_ids}

        if ATTR_TRANSITION in kwargs:
            data[ATTR_TRANSITION] = kwargs[ATTR_TRANSITION]

        try:
            if not data[ATTR_ENTITY_ID]:
                return
            if err := await self._async_call_light_service(SERVICE_TURN_OFF, data):
                raise err
        finally:
            if optimistic:
                self._async_schedule_post_command_sync()
            else:
                self._async_sync_group_state()

    @callback
    def async_update_group_state(self) -> None:
        """Query all members and determine the light group state."""
        self._update_assumed_state_from_members()
        snapshot = self._get_member_snapshot()
        valid_states = snapshot.valid_states
        on_states = snapshot.on_states

        if valid_states:
            self._attr_is_on = any(
                state.state == STATE_ON for state in valid_states
            )
            self._attr_available = True
        else:
            self._attr_is_on = None
            self._attr_available = bool(snapshot.enabled_entity_ids) and any(
                state.state != STATE_UNAVAILABLE for state in snapshot.enabled_states
            )

        # Brightness is calculated only from ON lights using average.
        self._attr_brightness = representative_brightness(
            on_states, ATTR_BRIGHTNESS
        )

        self._attr_hs_color = reduce_attribute(
            on_states, ATTR_HS_COLOR, reduce=mean_circle
        )
        self._attr_rgb_color = reduce_attribute(
            on_states, ATTR_RGB_COLOR, reduce=mean_tuple
        )
        self._attr_rgbw_color = reduce_attribute(
            on_states, ATTR_RGBW_COLOR, reduce=mean_tuple
        )
        self._attr_rgbww_color = reduce_attribute(
            on_states, ATTR_RGBWW_COLOR, reduce=mean_tuple
        )
        self._attr_xy_color = reduce_attribute(
            on_states, ATTR_XY_COLOR, reduce=mean_tuple
        )

        self._attr_color_temp_kelvin = reduce_attribute(
            on_states, ATTR_COLOR_TEMP_KELVIN
        )
        self._attr_min_color_temp_kelvin = reduce_attribute(
            valid_states, ATTR_MIN_COLOR_TEMP_KELVIN, default=2000, reduce=min
        )
        self._attr_max_color_temp_kelvin = reduce_attribute(
            valid_states, ATTR_MAX_COLOR_TEMP_KELVIN, default=6500, reduce=max
        )

        self._attr_effect_list = None
        all_effect_lists = list(find_state_attributes(valid_states, ATTR_EFFECT_LIST))
        if all_effect_lists:
            self._attr_effect_list = list(set().union(*all_effect_lists))
            self._attr_effect_list.sort()
            if "None" in self._attr_effect_list:
                self._attr_effect_list.remove("None")
                self._attr_effect_list.insert(0, "None")

        self._attr_effect = None
        all_effects = list(find_state_attributes(on_states, ATTR_EFFECT))
        if all_effects:
            effects_count = Counter(itertools.chain(all_effects))
            self._attr_effect = effects_count.most_common(1)[0][0]

        supported_color_modes = {ColorMode.ONOFF}
        all_supported_color_modes = list(
            find_state_attributes(valid_states, ATTR_SUPPORTED_COLOR_MODES)
        )
        if all_supported_color_modes:
            supported_color_modes = filter_supported_color_modes(
                cast(set[ColorMode], set().union(*all_supported_color_modes))
            )
        self._attr_supported_color_modes = supported_color_modes

        self._attr_color_mode = ColorMode.UNKNOWN
        all_color_modes = list(find_state_attributes(on_states, ATTR_COLOR_MODE))
        if all_color_modes:
            color_mode_count = Counter(itertools.chain(all_color_modes))
            if ColorMode.ONOFF in color_mode_count:
                if ColorMode.ONOFF in supported_color_modes:
                    color_mode_count[ColorMode.ONOFF] = -1
                else:
                    color_mode_count.pop(ColorMode.ONOFF)
            if ColorMode.BRIGHTNESS in color_mode_count:
                if ColorMode.BRIGHTNESS in supported_color_modes:
                    color_mode_count[ColorMode.BRIGHTNESS] = 0
                else:
                    color_mode_count.pop(ColorMode.BRIGHTNESS)
            if color_mode_count:
                self._attr_color_mode = color_mode_count.most_common(1)[0][0]
            else:
                self._attr_color_mode = next(iter(supported_color_modes))

        self._attr_supported_features = LightEntityFeature(0)
        for support in find_state_attributes(valid_states, ATTR_SUPPORTED_FEATURES):
            self._attr_supported_features |= support
        self._attr_supported_features &= SUPPORT_GROUP_LIGHT

        self._attr_extra_state_attributes = {ATTR_ENTITY_ID: self._entity_ids}
