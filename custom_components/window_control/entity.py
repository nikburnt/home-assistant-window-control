"""Shared window device metadata and runtime subscriptions."""

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN


class WindowEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, runtime, key, name):
        self.runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, runtime.entry.entry_id)},
            name=runtime.entry.title,
            manufacturer="Window Control",
            model="Window Controller",
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.runtime.listeners.add(self.async_write_ha_state)
        self.async_on_remove(
            lambda: self.runtime.listeners.discard(self.async_write_ha_state)
        )

    def entity_id_for(self, domain, key):
        return er.async_get(self.hass).async_get_entity_id(
            domain, DOMAIN, f"{self.runtime.entry.entry_id}_{key}"
        )
