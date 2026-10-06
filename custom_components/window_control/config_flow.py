"""UI configuration and reconfiguration for an ordered set of covers."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    AreaSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import DOMAIN, FEATURES


class WindowConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def _schema(self, defaults):
        fields = {
            vol.Required(
                "name", default=defaults.get("name", "Window")
            ): TextSelector(),
            vol.Required(
                "rollers", default=defaults.get("rollers", [])
            ): EntitySelector(EntitySelectorConfig(domain="cover", multiple=True)),
            vol.Optional(
                "curtain",
                **({"default": defaults["curtain"]} if defaults.get("curtain") else {}),
            ): EntitySelector(EntitySelectorConfig(domain="cover")),
            vol.Optional(
                "area_id",
                **({"default": defaults["area_id"]} if defaults.get("area_id") else {}),
            ): AreaSelector(),
            vol.Required(
                "stagger_seconds", default=defaults.get("stagger_seconds", 2)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=10,
                    step=0.1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Required(
                "travel_timeout", default=defaults.get("travel_timeout", 90)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=10,
                    max=300,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
        }
        return vol.Schema(fields)

    def _validate(self, data, current_id=None):
        sources = data["rollers"] + ([data["curtain"]] if data.get("curtain") else [])
        if not data["rollers"] or len(sources) != len(set(sources)):
            return {"base": "invalid_members"}
        owned = set()
        for entry in self._async_current_entries():
            if entry.entry_id != current_id:
                owned.update(entry.data["rollers"])
                if entry.data.get("curtain"):
                    owned.add(entry.data["curtain"])
        registry = er.async_get(self.hass)
        for source in sources:
            entity = registry.async_get(source)
            if source in owned or (entity and entity.platform == DOMAIN):
                return {"base": "already_owned"}
            state = self.hass.states.get(source)
            if (
                state
                and (state.attributes.get("supported_features", 0) & FEATURES)
                != FEATURES
            ):
                return {"base": "unsupported_cover"}
        return {}

    async def async_step_user(self, user_input=None):
        errors = self._validate(user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            return self.async_create_entry(title=user_input["name"], data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=self._schema(user_input or {}), errors=errors
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = (
            self._validate(user_input, entry.entry_id) if user_input is not None else {}
        )
        if user_input is not None and not errors:
            return self.async_update_reload_and_abort(
                entry, title=user_input["name"], data=user_input
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._schema(user_input or dict(entry.data)),
            errors=errors,
        )
