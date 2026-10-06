"""Standard cover entities for each layer and for the roller group."""

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)

from .entity import WindowEntity


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    async_add_entities(
        [WindowCover(runtime)]
        + [WindowCover(runtime, source) for source in runtime.members]
    )


class WindowCover(WindowEntity, CoverEntity):
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, runtime, source=None):
        self.source = source
        self.sources = [source] if source else list(runtime.entry.data["rollers"])
        member = runtime.members.get(source)
        super().__init__(
            runtime, source or "rollers", member.label if member else "Roller Shades"
        )
        self._attr_device_class = (
            CoverDeviceClass.CURTAIN
            if member and member.role == "curtain"
            else CoverDeviceClass.SHADE
        )

    @property
    def available(self):
        return any(self.runtime.available(s) for s in self.sources)

    @property
    def current_cover_position(self):
        positions = [
            self.runtime.position(s) if self.runtime.available(s) else None
            for s in self.sources
        ]
        return (
            positions[0]
            if positions[0] is not None and len(set(positions)) == 1
            else None
        )

    @property
    def is_closed(self):
        positions = [
            self.runtime.position(s) if self.runtime.available(s) else None
            for s in self.sources
        ]
        if all(p == 0 for p in positions):
            return True
        if any(p is not None and p > 0 for p in positions):
            return False
        states = [self.runtime.state(s) for s in self.sources]
        if all(s and s.state == "closed" for s in states):
            return True
        if any(s and s.state in ("open", "opening", "closing") for s in states):
            return False
        return None

    def _direction(self, direction):
        states = {
            s.state for source in self.sources if (s := self.runtime.state(source))
        }
        opposite = "closing" if direction == "opening" else "opening"
        return direction in states and opposite not in states

    @property
    def is_opening(self):
        return self._direction("opening")

    @property
    def is_closing(self):
        return self._direction("closing")

    @property
    def extra_state_attributes(self):
        if self.source:
            member = self.runtime.members[self.source]
            return {
                "source_entity_id": self.source,
                "phase": member.phase,
                "target_position": member.intent
                if isinstance(member.intent, int)
                else None,
                "last_error": member.error,
            }
        return {
            "window_control": True,
            "stagger_seconds": self.runtime.entry.data.get("stagger_seconds", 2),
            "members": [
                {
                    "entity_id": self.entity_id_for("cover", m.source),
                    "role": m.role,
                    "source_entity_id": m.source,
                    "phase": m.phase,
                    "error": m.error,
                }
                for m in self.runtime.members.values()
            ],
            "all_controls": {
                action: self.entity_id_for("button", action + "_all")
                for action in ("open", "close", "stop")
            },
        }

    async def async_open_cover(self, **kwargs):
        self.runtime.command(
            self.sources, 100, self._context, stagger=self.source is None
        )

    async def async_close_cover(self, **kwargs):
        self.runtime.command(
            self.sources, 0, self._context, stagger=self.source is None
        )

    async def async_stop_cover(self, **kwargs):
        self.runtime.command(self.sources, "stop", self._context)

    async def async_set_cover_position(self, **kwargs):
        self.runtime.command(
            self.sources, kwargs["position"], self._context, stagger=self.source is None
        )
