"""Per-window verbose logging, without changing global logger settings."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .entity import WindowEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [LoggingSwitch(entry.runtime_data, "verbose_logging", "Verbose Logging")]
    )


class LoggingSwitch(WindowEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:text-box-search-outline"

    @property
    def is_on(self):
        return self.runtime.verbose

    async def _set(self, enabled):
        entry = self.runtime.entry
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, "verbose_logging": enabled}
        )
        self.runtime.publish()

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)
