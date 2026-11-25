"""Fake lights: light.dimmable_a, light.dimmable_b and light.on_off."""

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity


class FakeLight(LightEntity):
    """A light that applies every command instantly."""

    _attr_should_poll = False

    def __init__(self, name: str, dimmable: bool) -> None:
        """Create an off light."""
        self._attr_name = name
        self._attr_unique_id = name
        self._attr_is_on = False
        mode = ColorMode.BRIGHTNESS if dimmable else ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}
        self._dimmable = dimmable

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on, keeping the last brightness when none is given."""
        self._attr_is_on = True
        if self._dimmable:
            self._attr_brightness = kwargs.get(
                ATTR_BRIGHTNESS, self._attr_brightness or 255
            )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off."""
        self._attr_is_on = False
        self.async_write_ha_state()


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Add the fake lights."""
    async_add_entities(
        [
            FakeLight("dimmable_a", True),
            FakeLight("dimmable_b", True),
            FakeLight("on_off", False),
        ]
    )
