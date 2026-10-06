"""Whole-window commands, also callable by scripts and automations."""

from homeassistant.components.button import ButtonEntity

from .entity import WindowEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            WindowButton(entry.runtime_data, action)
            for action in ("open", "close", "stop")
        ]
    )


class WindowButton(WindowEntity, ButtonEntity):
    def __init__(self, runtime, action):
        super().__init__(runtime, action + "_all", action.title() + " All")
        self.action = action
        self._attr_icon = {
            "open": "mdi:arrow-up",
            "close": "mdi:arrow-down",
            "stop": "mdi:stop",
        }[action]

    async def async_press(self):
        self.runtime.command(
            list(self.runtime.members),
            {"open": 100, "close": 0, "stop": "stop"}[self.action],
            self._context,
            stagger=True,
        )
