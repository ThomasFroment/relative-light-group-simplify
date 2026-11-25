"""Setup tests."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.relative_light_group_simplified.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


async def test_group_entity_created(hass: HomeAssistant) -> None:
    """A config entry creates the group light with its members."""
    assert await async_setup_component(
        hass, "light", {"light": [{"platform": "fake_light"}]}
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Group",
        options={"name": "Group", "entities": ["light.dimmable_a", "light.dimmable_b"]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("light.group")
    assert state is not None
    assert state.attributes["entity_id"] == ["light.dimmable_a", "light.dimmable_b"]
