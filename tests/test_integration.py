"""Configuration, public services, state accuracy and lifecycle checks."""

import asyncio

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


def settings():
    return {
        "name": "Wide Window",
        "rollers": ["cover.left", "cover.right"],
        "curtain": "cover.curtain",
        "stagger_seconds": 0,
        "travel_timeout": 90,
    }


def sources(hass):
    for source, position in (
        ("cover.left", 20),
        ("cover.right", 80),
        ("cover.curtain", 0),
    ):
        hass.states.async_set(
            source,
            "open" if position else "closed",
            {"current_position": position, "supported_features": 15},
        )


async def test_public_entities_and_no_startup_movement(hass):
    sources(hass)
    calls = []
    entry = MockConfigEntry(
        domain="window_control", title="Wide Window", data=settings()
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)

    def entity(domain, key):
        return registry.async_get_entity_id(
            domain, "window_control", entry.entry_id + "_" + key
        )

    group = entity("cover", "rollers")
    state = hass.states.get(group)
    assert state.attributes.get("current_position") is None
    assert len(state.attributes["members"]) == 3
    assert all(member["entity_id"] for member in state.attributes["members"])
    assert all(state.attributes["all_controls"].values())
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 8
    assert not entry.runtime_data.trace

    async def record(call):
        calls.append((call.service, call.data["entity_id"], call.data.get("position")))

    for service in ("open_cover", "close_cover", "stop_cover", "set_cover_position"):
        hass.services.async_register("cover", service, record)
    # Invoke the public entity directly: the source domain's handlers are test spies.
    component = hass.data["cover"]
    public = component.get_entity(entity("cover", "cover.right"))
    await public.async_set_cover_position(position=40)
    for _ in range(10):
        await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert calls == [("set_cover_position", "cover.right", 40)]
    hass.states.async_set(
        "cover.right", "open", {"current_position": 40, "supported_features": 15}
    )
    await hass.async_block_till_done()
    assert hass.states.get(public.entity_id).attributes["current_position"] == 40
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": entity("switch", "verbose_logging")},
        blocking=True,
    )
    assert entry.options["verbose_logging"]
    count = len(calls)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(calls) == count
    assert entry.runtime_data.verbose
    assert not entry.runtime_data.trace
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_config_flow_preserves_order(hass):
    sources(hass)
    result = await hass.config_entries.flow.async_init(
        "window_control", context={"source": "user"}
    )
    assert result["type"] == "form"
    data = settings()
    data["rollers"] = ["cover.right", "cover.left"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
    assert result["type"] == "create_entry"
    assert result["data"]["rollers"] == ["cover.right", "cover.left"]
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_config_flow_rejects_duplicate_sources(hass):
    sources(hass)
    result = await hass.config_entries.flow.async_init(
        "window_control", context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], settings() | {"curtain": "cover.left"}
    )
    assert result["errors"] == {"base": "invalid_members"}


async def test_config_flow_rejects_ownership_overlap(hass):
    sources(hass)
    existing = MockConfigEntry(
        domain="window_control", title="Existing", data=settings()
    )
    existing.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        "window_control", context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], settings()
    )
    assert result["errors"] == {"base": "already_owned"}


async def test_standard_services_reach_physical_covers(hass):
    from homeassistant.components.cover import CoverEntity
    from homeassistant.setup import async_setup_component

    calls = []

    class PhysicalCover(CoverEntity):
        _attr_should_poll = False
        _attr_supported_features = 15
        _attr_is_closed = False
        _attr_current_cover_position = 50

        def __init__(self, name):
            self.entity_id = "cover." + name
            self._attr_name = name

        async def async_open_cover(self, **kwargs):
            calls.append(("open", self.entity_id))

        async def async_close_cover(self, **kwargs):
            calls.append(("close", self.entity_id))

        async def async_set_cover_position(self, **kwargs):
            calls.append((kwargs["position"], self.entity_id))

        async def async_stop_cover(self, **kwargs):
            calls.append(("stop", self.entity_id))

    assert await async_setup_component(hass, "cover", {})
    await hass.data["cover"].async_add_entities(
        [PhysicalCover(name) for name in ("left", "right", "curtain")]
    )
    entry = MockConfigEntry(domain="window_control", title="Window", data=settings())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not calls
    registry = er.async_get(hass)
    button = registry.async_get_entity_id(
        "button", "window_control", entry.entry_id + "_open_all"
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    for _ in range(20):
        await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert calls == [
        ("open", "cover.left"),
        ("open", "cover.right"),
        ("open", "cover.curtain"),
    ]
    individual = registry.async_get_entity_id(
        "cover", "window_control", entry.entry_id + "_cover.right"
    )
    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": individual, "position": 25},
        blocking=True,
    )
    for _ in range(20):
        await asyncio.sleep(0)
    await hass.async_block_till_done()
    assert calls[-1] == (25, "cover.right")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_removes_retired_proxies(hass):
    sources(hass)
    entry = MockConfigEntry(domain="window_control", title="Window", data=settings())
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    retired = registry.async_get_entity_id(
        "cover", "window_control", entry.entry_id + "_cover.right"
    )
    assert retired
    result = await hass.config_entries.flow.async_init(
        "window_control", context={"source": "reconfigure", "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], settings() | {"rollers": ["cover.left"]}
    )
    assert result["type"] == "abort" and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert registry.async_get(retired) is None
    assert hass.states.get(retired) is None
    assert not entry.runtime_data.trace
    await hass.config_entries.async_unload(entry.entry_id)
