"""Timing and replacement tests use simulated covers, never real hardware."""

import asyncio
from datetime import timedelta

import pytest
from homeassistant.core import Context
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.window_control.runtime import WindowRuntime

SOURCES = ["cover.left", "cover.middle", "cover.right"]


@pytest.fixture
async def window(hass):
    for source in [*SOURCES, "cover.curtain"]:
        hass.states.async_set(
            source, "closed", {"current_position": 0, "supported_features": 15}
        )
    entry = MockConfigEntry(
        domain="window_control",
        title="Window",
        data={
            "rollers": SOURCES,
            "curtain": "cover.curtain",
            "stagger_seconds": 2,
            "travel_timeout": 90,
        },
    )
    entry.add_to_hass(hass)
    runtime = WindowRuntime(hass, entry)
    runtime.start()
    calls = []

    async def record(call):
        calls.append(
            (
                call.service,
                call.data["entity_id"],
                call.data.get("position"),
                call.context,
            )
        )

    for service in ("open_cover", "close_cover", "stop_cover", "set_cover_position"):
        hass.services.async_register("cover", service, record)
    yield runtime, calls
    await runtime.close()


async def flush(hass):
    for _ in range(12):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


async def advance(hass, seconds):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await flush(hass)


async def test_cascade_and_curtain_start_together(hass, window):
    runtime, calls = window
    runtime.command(runtime.members, 100, stagger=True)
    await flush(hass)
    assert [c[1] for c in calls] == ["cover.left", "cover.curtain"]
    await advance(hass, 2.1)
    assert [c[1] for c in calls] == ["cover.left", "cover.curtain", "cover.middle"]
    await advance(hass, 4.1)
    assert calls[-1][1] == "cover.right"


async def test_repeated_intent_does_not_reset_cascade(hass, window):
    runtime, calls = window
    runtime.command(SOURCES, 100, stagger=True)
    timer = runtime.members["cover.right"].timer
    await flush(hass)
    runtime.command(SOURCES, 100, stagger=True)
    assert runtime.members["cover.right"].timer is timer
    await advance(hass, 4.1)
    assert len(calls) == 3


async def test_individual_override_does_not_cancel_other_members(hass, window):
    runtime, calls = window
    runtime.command(SOURCES, 100, stagger=True)
    runtime.command(["cover.right"], 35)
    await flush(hass)
    assert [(c[0], c[1], c[2]) for c in calls] == [
        ("open_cover", "cover.left", None),
        ("set_cover_position", "cover.right", 35),
    ]
    await advance(hass, 4.1)
    assert [c[1] for c in calls] == ["cover.left", "cover.right", "cover.middle"]


async def test_stop_cancels_all_pending_starts(hass, window):
    runtime, calls = window
    runtime.command(runtime.members, 100, stagger=True)
    await flush(hass)
    runtime.command(runtime.members, "stop")
    await flush(hass)
    await advance(hass, 5)
    assert sum(c[0] == "open_cover" for c in calls) == 2
    assert sum(c[0] == "stop_cover" for c in calls) == 4


async def test_reverse_is_immediate_and_no_old_starts(hass, window):
    runtime, calls = window
    for source in SOURCES:
        hass.states.async_set(source, "open", {"current_position": 50})
    runtime.command(SOURCES, 100, stagger=True)
    await flush(hass)
    runtime.command(SOURCES, 0, stagger=True)
    await flush(hass)
    assert [c[0] for c in calls] == [
        "open_cover",
        "close_cover",
        "close_cover",
        "close_cover",
    ]
    await advance(hass, 5)
    assert len(calls) == 4


async def test_offline_member_does_not_block_or_replay(hass, window):
    runtime, calls = window
    hass.states.async_set("cover.middle", "unavailable")
    await flush(hass)
    runtime.command(SOURCES, 100, stagger=True)
    await advance(hass, 5)
    assert {c[1] for c in calls} == {"cover.left", "cover.right"}
    hass.states.async_set("cover.middle", "closed", {"current_position": 0})
    await flush(hass)
    assert len(calls) == 2
    runtime.command(SOURCES, 100, stagger=True)
    await advance(hass, 5)
    assert calls[-1][1] == "cover.middle"
    assert len(calls) == 3


async def test_commands_wait_for_real_position_confirmation(hass, window):
    runtime, _calls = window
    runtime.command(["cover.left"], 70)
    await flush(hass)
    assert runtime.members["cover.left"].phase == "awaiting_confirmation"
    hass.states.async_set("cover.left", "open", {"current_position": 70})
    await flush(hass)
    assert runtime.members["cover.left"].phase == "idle"
    assert runtime.members["cover.left"].intent is None


async def test_confirmation_timeout_allows_retry(hass, window):
    runtime, calls = window
    runtime.command(["cover.left"], 100)
    await flush(hass)
    await advance(hass, 91)
    assert runtime.members["cover.left"].error == "confirmation_timeout"
    runtime.command(["cover.left"], 100)
    await flush(hass)
    assert len(calls) == 2


async def test_latest_only_while_transport_is_busy(hass, window):
    runtime, calls = window
    release = asyncio.Event()
    entered = asyncio.Event()

    async def slow(call):
        calls.append((call.service, call.data["entity_id"], None, call.context))
        entered.set()
        await release.wait()

    hass.services.async_register("cover", "open_cover", slow)
    runtime.command(["cover.left"], 100)
    await entered.wait()
    runtime.command(["cover.left"], 30)
    context = Context()
    runtime.command(["cover.left"], "stop", context)
    release.set()
    await flush(hass)
    assert [c[0] for c in calls] == ["open_cover", "stop_cover"]
    assert calls[-1][3] == context


async def test_service_failure_is_isolated(hass, window):
    runtime, calls = window

    async def sometimes_fail(call):
        if call.data["entity_id"] == "cover.left":
            raise RuntimeError("Test transport failure")
        calls.append((call.service, call.data["entity_id"]))

    hass.services.async_register("cover", "open_cover", sometimes_fail)
    runtime.command(SOURCES, 100)
    await flush(hass)
    assert runtime.members["cover.left"].error == "RuntimeError"
    assert {c[1] for c in calls} == {"cover.middle", "cover.right"}


async def test_unload_cancels_pending_without_motor_commands(hass, window):
    runtime, calls = window
    runtime.command(SOURCES, 100, stagger=True)
    await flush(hass)
    await runtime.close()
    await advance(hass, 5)
    assert len(calls) == 1


async def test_offline_during_pending_never_starts_on_recovery(hass, window):
    runtime, calls = window
    runtime.command(SOURCES, 100, stagger=True)
    await flush(hass)
    hass.states.async_set("cover.right", "unavailable")
    await flush(hass)
    hass.states.async_set("cover.right", "closed", {"current_position": 0})
    await advance(hass, 5)
    assert [c[1] for c in calls] == ["cover.left", "cover.middle"]


async def test_individual_stop_keeps_other_pending_members(hass, window):
    runtime, calls = window
    runtime.command(SOURCES, 100, stagger=True)
    await flush(hass)
    runtime.command(["cover.right"], "stop")
    await advance(hass, 5)
    assert [(c[0], c[1]) for c in calls] == [
        ("open_cover", "cover.left"),
        ("stop_cover", "cover.right"),
        ("open_cover", "cover.middle"),
    ]


async def test_real_state_changes_do_not_issue_commands(hass, window):
    runtime, calls = window
    hass.states.async_set("cover.left", "opening", {"current_position": 30})
    await flush(hass)
    hass.states.async_set("cover.left", "open", {"current_position": 90})
    await flush(hass)
    assert not calls
    assert runtime.position("cover.left") == 90


@pytest.mark.parametrize("target", [-1, 101, True, 1.5, "open"])
async def test_invalid_targets_do_not_change_intents(hass, window, target):
    runtime, calls = window
    with pytest.raises(ValueError):
        runtime.command(SOURCES, target)
    assert not calls
    assert all(m.intent is None for m in runtime.members.values())
