"""Provide entity classes for relative group entities."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable, Mapping
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import start
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_state_change_event


class GroupEntity(Entity):
    """Representation of a Group of entities."""

    _unrecorded_attributes = frozenset({ATTR_ENTITY_ID})

    _attr_should_poll = False
    _entity_ids: list[str]

    @callback
    def async_start_preview(
        self,
        preview_callback: Callable[[str, Mapping[str, Any]], None],
    ) -> CALLBACK_TYPE:
        """Render a preview."""

        @callback
        def async_state_changed_listener(
            event: Event[EventStateChangedData] | None,
        ) -> None:
            """Handle child updates."""
            self.async_update_group_state()
            calculated_state = self._async_calculate_state()
            preview_callback(calculated_state.state, calculated_state.attributes)

        async_state_changed_listener(None)
        return async_track_state_change_event(
            self.hass, self._entity_ids, async_state_changed_listener
        )

    async def async_added_to_hass(self) -> None:
        """Register listeners."""

        @callback
        def async_state_changed_listener(
            event: Event[EventStateChangedData],
        ) -> None:
            """Handle child updates."""
            self.async_set_context(event.context)
            self.async_member_state_changed(event)
            if self.async_should_defer_state_change(event):
                return
            self.async_defer_or_update_ha_state()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._entity_ids, async_state_changed_listener
            )
        )
        self.async_on_remove(start.async_at_start(self.hass, self._update_at_start))

    @callback
    def _update_at_start(self, _: HomeAssistant) -> None:
        """Update the group state at start."""
        self.async_update_group_state()
        self.async_write_ha_state()

    @callback
    def async_defer_or_update_ha_state(self) -> None:
        """Refresh derived state from members and write it."""
        if not self.hass.is_running:
            return

        self.async_update_group_state()
        self.async_write_ha_state()

    @callback
    def async_should_defer_state_change(
        self,
        event: Event[EventStateChangedData],
    ) -> bool:
        """Return True when a member event should be handled later."""
        return False

    @abstractmethod
    @callback
    def async_update_group_state(self) -> None:
        """Refresh state from member entities."""

    @callback
    def async_member_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle a member state change before the group state is refreshed."""
