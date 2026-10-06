"""Window Control setup. Loading an entry never moves a cover."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PLATFORMS
from .runtime import WindowRuntime


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = entry.runtime_data = WindowRuntime(hass, entry)
    entity_registry = er.async_get(hass)
    allowed = {
        f"{entry.entry_id}_{key}"
        for key in [
            *runtime.members,
            "rollers",
            "open_all",
            "close_all",
            "stop_all",
            "verbose_logging",
        ]
    }
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.unique_id not in allowed:
            entity_registry.async_remove(entity.entity_id)
    runtime.start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    registry = dr.async_get(hass)
    if device := registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)}):
        registry.async_update_device(device.id, area_id=entry.data.get("area_id"))
    runtime.publish()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.close()
    return True
