"""Behaviour tests for the relative light group."""

from datetime import timedelta
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.relative_light_group_simplified import (
    const,
    light as group_light,
)
from custom_components.relative_light_group_simplified.const import DOMAIN
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import homeassistant.util.dt as dt_util

A = "light.dimmable_a"
B = "light.dimmable_b"
C = "light.on_off"
G = "light.group"
ROOT = Path(__file__).parent.parent
COMPONENT = ROOT / "custom_components" / "relative_light_group_simplified"


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()


async def _setup(hass: HomeAssistant, members: list[str]) -> MockConfigEntry:
    assert await async_setup_component(
        hass, "light", {"light": [{"platform": "fake_light"}]}
    )
    await hass.async_block_till_done()
    entry = MockConfigEntry(
        domain=DOMAIN, title="Group", options={"name": "Group", "entities": members}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _on(hass: HomeAssistant, entity_id: str, context=None, **data) -> None:
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_id, **data},
        blocking=True,
        context=context,
    )
    await _settle(hass)


async def _off(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "light", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    await _settle(hass)


def _b(hass: HomeAssistant, entity_id: str):
    return hass.states.get(entity_id).attributes.get("brightness")


def _levels(hass: HomeAssistant) -> tuple:
    return _b(hass, A), _b(hass, B)


async def test_ratio_kept_at_100_percent(hass: HomeAssistant) -> None:
    await _setup(hass, [A, B])
    await _on(hass, A, brightness=255)
    await _on(hass, B, brightness=100)
    await _on(hass, G, brightness=255)
    assert _levels(hass) == (255, 255)
    await _on(hass, G, brightness=89)
    assert _levels(hass) == (128, 50)


async def test_ratio_kept_with_on_off_member(hass: HomeAssistant) -> None:
    await _setup(hass, [A, B, C])
    await _on(hass, C)
    await _on(hass, A, brightness=200)
    await _on(hass, B, brightness=50)
    await _on(hass, G, brightness=255)
    await _on(hass, G, brightness=60)
    assert _levels(hass) == (96, 24)
    assert hass.states.get(C).state == "on"


async def test_ratio_kept_at_minimum(hass: HomeAssistant) -> None:
    await _setup(hass, [A, B])
    await _on(hass, A, brightness=255)
    await _on(hass, B, brightness=100)
    await _on(hass, G, brightness=1)
    await _on(hass, G, brightness=89)
    assert _levels(hass) == (128, 50)


async def test_ratio_kept_across_group_off_on(hass: HomeAssistant) -> None:
    await _setup(hass, [A, B])
    await _on(hass, A, brightness=255)
    await _on(hass, B, brightness=100)
    await _on(hass, G, brightness=255)
    await _off(hass, G)
    await _on(hass, G)
    assert _levels(hass) == (255, 255)
    await _on(hass, G, brightness=89)
    assert _levels(hass) == (128, 50)


async def test_group_on_turns_on_all_and_late_lamp_joins(hass: HomeAssistant) -> None:
    await _setup(hass, [A, B])
    await _on(hass, G)
    assert hass.states.get(A).state == "on"
    assert hass.states.get(B).state == "on"


async def test_group_on_with_brightness_replaces_ratio(hass: HomeAssistant) -> None:
    await _setup(hass, [A, B])
    await _on(hass, A, brightness=204)
    await _on(hass, B, brightness=51)
    await _on(hass, G, brightness=150)
    await _off(hass, G)
    await _on(hass, G, brightness=128)
    assert _levels(hass) == (128, 128)
    await _on(hass, G, brightness=140)
    a, b = _levels(hass)
    assert abs(a - b) <= 1


async def test_outside_change_becomes_new_ratio(hass: HomeAssistant) -> None:
    await _setup(hass, [A, B])
    await _on(hass, A, brightness=200)
    await _on(hass, B, brightness=200)
    await _on(hass, G, brightness=255)
    await _on(hass, B, brightness=50)
    await _on(hass, G, brightness=100)
    a, b = _levels(hass)
    assert a > 2 * b


async def test_old_group_context_counts_as_outside(hass: HomeAssistant) -> None:
    await _setup(hass, [A, B])
    await _on(hass, A, brightness=200)
    await _on(hass, B, brightness=200)
    offset = [0.0]
    fake_time = SimpleNamespace(monotonic=lambda: time.monotonic() + offset[0])
    with patch.object(group_light, "time", fake_time):
        ctx = Context()
        await _on(hass, G, context=ctx, brightness=255)
        offset[0] = 30.0
        await _on(hass, B, context=ctx, brightness=50)
        await _on(hass, G, brightness=100)
    a, b = _levels(hass)
    assert a > 2 * b


async def test_lamp_turned_off_outside_leaves_ratio(hass: HomeAssistant) -> None:
    await _setup(hass, [A, B])
    await _on(hass, A, brightness=200)
    await _on(hass, B, brightness=100)
    await _on(hass, G, brightness=255)
    await _off(hass, B)
    await _on(hass, G, brightness=100)
    assert hass.states.get(B).state == "off"
    assert _b(hass, A) == 100


async def test_lamp_turned_on_outside_joins_at_current_level(
    hass: HomeAssistant,
) -> None:
    await _setup(hass, [A, B])
    await _on(hass, A, brightness=200)
    await _off(hass, B)
    await _on(hass, B, brightness=100)
    await _on(hass, G, brightness=100)
    a, b = _levels(hass)
    assert a == pytest.approx(2 * b, abs=2)


async def test_yaml_setup_not_supported(hass: HomeAssistant, caplog) -> None:
    await async_setup_component(hass, DOMAIN, {DOMAIN: {}})
    await hass.async_block_till_done()
    assert "does not support YAML setup" in caplog.text
    issue = ir.async_get(hass).async_get_issue(
        "homeassistant", f"config_entry_only_{DOMAIN}"
    )
    assert issue is not None
    assert DOMAIN not in hass.data


async def test_group_icon_by_state(hass: HomeAssistant) -> None:
    await _setup(hass, [A, B])
    await _off(hass, G)
    icons = json.loads((COMPONENT / "icons.json").read_text())
    state = icons["entity"]["light"]["relative_light_group"]
    assert state["default"] == "mdi:lightbulb-group"
    assert state["state"]["off"] == "mdi:lightbulb-group-off"
    assert "icon" not in hass.states.get(G).attributes


@pytest.mark.parametrize(
    ("members", "error"),
    [
        (["light.nope"], "unknown_entity"),
        ([], "empty_group"),
    ],
)
async def test_config_flow_errors_have_real_messages(
    hass: HomeAssistant, members, error
) -> None:
    assert await async_setup_component(
        hass, "light", {"light": [{"platform": "fake_light"}]}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "G", "entities": members}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    strings = json.loads((COMPONENT / "strings.json").read_text())
    for section in ("config", "options"):
        message = strings[section]["error"][error]
        assert message
        assert "{" not in message


def test_error_strings_have_no_placeholder() -> None:
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT / name).read_text())
        for section in ("config", "options"):
            for message in data[section]["error"].values():
                assert "{" not in message
        assert "exceptions" not in data


def test_const_only_domain_and_debounce() -> None:
    public = {n for n in dir(const) if n.isupper()}
    assert public == {"DOMAIN", "DEFAULT_DEBOUNCE_TIME"}


def test_manifest_and_hacs() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    base = "https://github.com/ThomasFroment/relative-light-group-simplify"
    assert manifest["documentation"] == base
    assert manifest["issue_tracker"] == f"{base}/issues"
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert "github_user" not in hacs
    assert hacs["homeassistant"] == "2026.5.0"


def test_validate_workflow() -> None:
    text = (ROOT / ".github/workflows/validate.yml").read_text()
    for needle in (
        "push",
        "pull_request",
        "schedule",
        "workflow_dispatch",
        "hacs/action",
        "hassfest",
        "integration",
    ):
        assert needle in text


def test_readme_has_no_known_issues() -> None:
    assert "Known issues" not in (ROOT / "README.md").read_text()
