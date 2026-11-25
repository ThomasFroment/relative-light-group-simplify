"""Shared test fixtures."""

# pytest-homeassistant-custom-component 0.13.375 targets Home Assistant 2026.10;
# these shims let it run on 2026.11. They do nothing once the plugin catches up.
import pytest

import homeassistant  # noqa: F401  # must alias httpx before anything imports it
import homeassistant.components.logger as ha_logger
from homeassistant.components.logger import helpers as ha_logger_helpers

if not hasattr(ha_logger, "_clear_logger_overwrites"):
    ha_logger._clear_logger_overwrites = ha_logger_helpers._clear_logger_overwrites

# Import this repo's custom_components before Home Assistant mounts the test
# plugin's own testing_config, which would otherwise shadow it.
from custom_components.relative_light_group_simplified.const import (  # noqa: E402, F401
    DOMAIN,
)

pytest_plugins = ["pytest_homeassistant_custom_component.plugins"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load the integrations under custom_components."""
    return
