"""Config flow for Relative Light Group integration."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
from typing import Any, cast

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ENTITIES
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er, selector
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaCommonFlowHandler,
    SchemaConfigFlowHandler,
    SchemaFlowError,
    SchemaFlowFormStep,
    SchemaOptionsFlowHandler,
    entity_selector_without_own_entities,
)

from .const import DOMAIN
from .entity import GroupEntity
from .light import async_create_preview_light

LIGHT_DOMAIN = "light"


@callback
def _entity_relative_light_group_entry(
    hass: HomeAssistant, entity_id: str
) -> ConfigEntry | None:
    """Return the relative_light_group config entry that owns the entity, if any."""
    registry = er.async_get(hass)
    reg_entry = registry.async_get(entity_id)
    if reg_entry is None or reg_entry.config_entry_id is None:
        return None
    config_entry = hass.config_entries.async_get_entry(reg_entry.config_entry_id)
    if config_entry is None or config_entry.domain != DOMAIN:
        return None
    return config_entry


@callback
def validate_member_entities(
    hass: HomeAssistant,
    entity_ids: list[Any],
    own_entity_ids: set[str] | None = None,
) -> list[str]:
    """Validate proposed member entity_ids."""
    own = own_entity_ids or set()
    seen: set[str] = set()
    deduped: list[str] = []

    for raw in entity_ids:
        if not isinstance(raw, str) or not raw:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_entity",
                translation_placeholders={"entity_id": str(raw)},
            )
        eid = raw
        if eid in seen:
            continue
        seen.add(eid)

        domain, _, _ = eid.partition(".")
        if domain != LIGHT_DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_domain",
                translation_placeholders={"entity_id": eid},
            )

        if eid in own:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="self_reference",
                translation_placeholders={"entity_id": eid},
            )

        registry = er.async_get(hass)
        if registry.async_get(eid) is None and hass.states.get(eid) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_entity",
                translation_placeholders={"entity_id": eid},
            )

        if _entity_relative_light_group_entry(hass, eid) is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="nested_relative_group",
                translation_placeholders={"entity_id": eid},
            )

        deduped.append(eid)

    return deduped



def light_config_schema() -> vol.Schema:
    """Generate config schema for light groups."""
    return vol.Schema(
        {
            vol.Required("name"): selector.TextSelector(),
            vol.Required(CONF_ENTITIES): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="light", multiple=True, reorder=True
                ),
            ),
        }
    )


async def light_options_schema(
    handler: SchemaCommonFlowHandler | None,
) -> vol.Schema:
    """Generate options schema for light groups."""
    entity_selector_field: selector.Selector[Any] | vol.Schema
    if handler is None:
        entity_selector_field = selector.selector(
            {"entity": {"domain": "light", "multiple": True, "reorder": True}}
        )
    else:
        entity_selector_field = entity_selector_without_own_entities(
            cast(SchemaOptionsFlowHandler, handler.parent_handler),
            selector.EntitySelectorConfig(
                domain="light", multiple=True, reorder=True
            ),
        )

    return vol.Schema(
        {
            vol.Required(CONF_ENTITIES): entity_selector_field,
        }
    )


LIGHT_CONFIG_SCHEMA = light_config_schema()


async def _async_validate_entities(
    handler: SchemaCommonFlowHandler, user_input: dict[str, Any]
) -> dict[str, Any]:
    """Reject members with invalid domain, self-references or nested groups."""
    entities = user_input.get(CONF_ENTITIES, [])

    hass = handler.parent_handler.hass
    own_entity_ids: set[str] = set()
    parent = handler.parent_handler
    if isinstance(parent, SchemaOptionsFlowHandler):
        registry = er.async_get(hass)
        own_entity_ids = {
            entry.entity_id
            for entry in er.async_entries_for_config_entry(
                registry, parent.config_entry.entry_id
            )
        }

    try:
        validated = validate_member_entities(
            hass, list(entities), own_entity_ids
        )
    except ServiceValidationError as err:
        raise SchemaFlowError(err.translation_key or "invalid_entities") from err

    if not validated:
        raise SchemaFlowError("empty_group")

    user_input[CONF_ENTITIES] = validated
    return user_input


CONFIG_FLOW = {
    "user": SchemaFlowFormStep(
        LIGHT_CONFIG_SCHEMA,
        validate_user_input=_async_validate_entities,
        preview="relative_light_group_simplified",
    ),
}

OPTIONS_FLOW = {
    "init": SchemaFlowFormStep(
        light_options_schema,
        validate_user_input=_async_validate_entities,
        preview="relative_light_group_simplified",
    ),
}

PREVIEW_OPTIONS_SCHEMA: dict[str, vol.Schema] = {}

CREATE_PREVIEW_ENTITY: dict[
    str,
    Callable[[HomeAssistant, str, dict[str, Any]], GroupEntity],
] = {
    "light": async_create_preview_light,
}


class RelativeLightGroupConfigFlowHandler(SchemaConfigFlowHandler, domain=DOMAIN):
    """Handle a config or options flow for relative light groups."""

    config_flow = CONFIG_FLOW
    options_flow = OPTIONS_FLOW
    options_flow_reloads = True

    @callback
    def async_config_entry_title(self, options: Mapping[str, Any]) -> str:
        """Return config entry title."""
        return cast(str, options["name"]) if "name" in options else ""

    @staticmethod
    async def async_setup_preview(hass: HomeAssistant) -> None:
        """Set up preview WS API."""
        schema = cast(
            Callable[
                [SchemaCommonFlowHandler | None],
                Coroutine[Any, Any, vol.Schema],
            ],
            OPTIONS_FLOW["init"].schema,
        )
        PREVIEW_OPTIONS_SCHEMA["light"] = await schema(None)
        websocket_api.async_register_command(hass, ws_start_preview)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "relative_light_group_simplified/start_preview",
        vol.Required("flow_id"): str,
        vol.Required("flow_type"): vol.Any("config_flow", "options_flow"),
        vol.Required("user_input"): dict,
    }
)
@callback
def ws_start_preview(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Generate a preview."""
    entity_registry_entry: er.RegistryEntry | None = None
    if msg["flow_type"] == "config_flow":
        flow_status = hass.config_entries.flow.async_get(msg["flow_id"])
        form_step = cast(SchemaFlowFormStep, CONFIG_FLOW["user"])
        schema = cast(vol.Schema, form_step.schema)
        validated = schema(msg["user_input"])
        name = validated["name"]
    else:
        flow_status = hass.config_entries.options.async_get(msg["flow_id"])
        config_entry_id = flow_status["handler"]
        config_entry = hass.config_entries.async_get_entry(config_entry_id)
        if not config_entry:
            raise HomeAssistantError

        name = config_entry.options["name"]
        validated = PREVIEW_OPTIONS_SCHEMA["light"](msg["user_input"])
        entity_registry = er.async_get(hass)
        entries = er.async_entries_for_config_entry(entity_registry, config_entry_id)
        if entries:
            entity_registry_entry = entries[0]

    @callback
    def async_preview_updated(state: str, attributes: Mapping[str, Any]) -> None:
        """Forward config entry state events to websocket."""
        connection.send_message(
            websocket_api.event_message(
                msg["id"], {"attributes": attributes, "state": state}
            )
        )

    preview_entity = CREATE_PREVIEW_ENTITY["light"](hass, name, validated)
    preview_entity.hass = hass
    preview_entity.registry_entry = entity_registry_entry

    connection.send_result(msg["id"])
    connection.subscriptions[msg["id"]] = preview_entity.async_start_preview(
        async_preview_updated
    )