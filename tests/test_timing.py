"""Travel estimates never replace source positions or command confirmation."""

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util
from test_runtime import SOURCES, advance, flush
from test_runtime import (
    window as window,  # noqa: PLC0414 - Re-export the shared fixture.
)


def configure(hass, runtime):
    hass.config_entries.async_update_entry(
        runtime.entry,
        options={
            "travel_times": {
                source: {"opening_time": opening, "closing_time": closing}
                for source, opening, closing in [
                    ("cover.left", 33, 30),
                    ("cover.middle", 33, 30),
                    ("cover.right", 41, 40),
                ]
            }
        },
    )
    runtime.update_options()


@pytest.mark.parametrize(
    "start,target,durations",
    [(0, 100, [33, 33, 41]), (100, 0, [30, 30, 40]), (20, 70, [16.5, 16.5, 20.5])],
)
async def test_direction_and_partial_travel(
    hass, window, freezer, start, target, durations
):
    runtime, _ = window
    configure(hass, runtime)
    for source in SOURCES:
        hass.states.async_set(
            source, "open" if start else "closed", {"current_position": start}
        )
    await flush(hass)
    now = dt_util.utcnow()
    runtime.command(SOURCES, target)
    await flush(hass)
    for source, seconds in zip(SOURCES, durations, strict=True):
        assert runtime.members[source].estimated_completion == now + timedelta(
            seconds=seconds
        )
        assert runtime.position(source) == start


async def test_repeated_group_command_restarts_estimates_on_dispatch(
    hass, window, freezer
):
    runtime, calls = window
    configure(hass, runtime)
    now = dt_util.utcnow()
    runtime.command(SOURCES, 100, stagger=True)
    await flush(hass)
    assert runtime.members["cover.left"].estimated_completion == now + timedelta(
        seconds=33
    )
    assert runtime.members["cover.right"].estimated_completion is None
    freezer.tick(2)
    await advance(hass, 0)
    assert runtime.members["cover.middle"].estimated_completion == now + timedelta(
        seconds=35
    )
    runtime.command(SOURCES, 100, stagger=True)
    assert runtime.members["cover.left"].estimated_completion is None
    assert runtime.members["cover.middle"].estimated_completion is None
    await flush(hass)
    assert runtime.members["cover.left"].estimated_completion == now + timedelta(
        seconds=35
    )
    freezer.tick(2)
    await advance(hass, 0)
    assert runtime.members["cover.middle"].estimated_completion == now + timedelta(
        seconds=37
    )
    assert runtime.members["cover.right"].estimated_completion is None
    freezer.tick(2)
    await advance(hass, 0)
    assert runtime.members["cover.right"].estimated_completion == now + timedelta(
        seconds=47
    )
    assert [c[1] for c in calls] == [
        "cover.left",
        "cover.middle",
        "cover.left",
        "cover.middle",
        "cover.right",
    ]


async def test_reports_correct_estimate_but_unrelated_updates_do_not(
    hass, window, freezer
):
    runtime, _ = window
    configure(hass, runtime)
    now = dt_util.utcnow()
    runtime.command(["cover.left"], 100)
    await flush(hass)
    freezer.tick(10)
    hass.states.async_set("cover.left", "opening", {"current_position": 50})
    await flush(hass)
    expected = now + timedelta(seconds=26.5)
    assert runtime.members["cover.left"].estimated_completion == expected
    freezer.tick(2)
    hass.states.async_set(
        "cover.left", "opening", {"current_position": 50, "battery": 99}
    )
    await flush(hass)
    assert runtime.members["cover.left"].estimated_completion == expected
    assert runtime.position("cover.left") == 50


async def test_override_and_stop_affect_only_selected_estimate(hass, window, freezer):
    runtime, _ = window
    configure(hass, runtime)
    runtime.command(SOURCES, 100)
    await flush(hass)
    middle = runtime.members["cover.middle"].estimated_completion
    hass.states.async_set("cover.right", "opening", {"current_position": 50})
    await flush(hass)
    runtime.command(["cover.right"], 0)
    assert runtime.members["cover.right"].estimated_completion is None
    await flush(hass)
    assert runtime.members[
        "cover.right"
    ].estimated_completion == dt_util.utcnow() + timedelta(seconds=20)
    runtime.command(["cover.right"], "stop")
    assert runtime.members["cover.right"].estimated_completion is None
    assert runtime.members["cover.middle"].estimated_completion == middle


async def test_expired_estimate_is_not_confirmation(hass, window, freezer):
    runtime, _ = window
    configure(hass, runtime)
    runtime.command(["cover.left"], 100)
    await flush(hass)
    freezer.tick(34)
    await advance(hass, 0)
    member = runtime.members["cover.left"]
    assert member.estimated_completion < dt_util.utcnow()
    assert member.phase == "awaiting_confirmation"
    assert runtime.position("cover.left") == 0
    hass.states.async_set("cover.left", "open", {"current_position": 100})
    await flush(hass)
    assert member.phase == "idle"
    assert member.estimated_completion is None


async def test_unknown_unavailable_and_missing_calibration(hass, window):
    runtime, _ = window
    configure(hass, runtime)
    runtime.command(runtime.members, 100)
    await flush(hass)
    assert runtime.members["cover.curtain"].estimated_completion is None
    hass.states.async_set("cover.left", "unknown", {"current_position": 0})
    hass.states.async_set("cover.right", "unavailable")
    await flush(hass)
    assert runtime.members["cover.left"].estimated_completion is None
    assert runtime.members["cover.right"].estimated_completion is None
    hass.states.async_set("cover.left", "unknown", {"current_position": 100})
    await flush(hass)
    assert runtime.members["cover.left"].phase == "awaiting_confirmation"


async def test_timeout_clears_estimate(hass, window, freezer):
    runtime, _ = window
    configure(hass, runtime)
    runtime.command(["cover.left"], 100)
    await flush(hass)
    freezer.tick(91)
    await advance(hass, 0)
    assert runtime.members["cover.left"].estimated_completion is None
    assert runtime.members["cover.left"].error == "confirmation_timeout"


async def test_estimate_clears_on_service_failure_and_unload(hass, window):
    runtime, _ = window
    configure(hass, runtime)

    async def fail(call):
        raise RuntimeError("Transport failed")

    hass.services.async_register("cover", "open_cover", fail)
    runtime.command(["cover.left"], 100)
    await flush(hass)
    assert runtime.members["cover.left"].estimated_completion is None
    runtime.command(["cover.right"], 50)
    await flush(hass)
    assert runtime.members["cover.right"].estimated_completion is not None
    await runtime.close()
    assert runtime.members["cover.right"].estimated_completion is None


async def test_logging_option_does_not_reset_active_estimate(hass, window, freezer):
    runtime, _ = window
    configure(hass, runtime)
    runtime.command(SOURCES, 100)
    await flush(hass)
    before = runtime.members["cover.left"].estimated_completion
    freezer.tick(5)
    hass.config_entries.async_update_entry(
        runtime.entry, options={**runtime.entry.options, "verbose_logging": True}
    )
    runtime.update_options()
    assert runtime.members["cover.left"].estimated_completion == before


async def test_unknown_position_recovery_restores_estimate_without_dispatch(
    hass, window
):
    runtime, calls = window
    configure(hass, runtime)
    hass.states.async_set("cover.left", "unknown", {"current_position": 0})
    await flush(hass)
    runtime.command(["cover.left"], 100)
    await flush(hass)
    assert runtime.members["cover.left"].estimated_completion is None
    hass.states.async_set("cover.left", "open", {"current_position": 0})
    await flush(hass)
    assert runtime.members["cover.left"].estimated_completion is not None
    assert len(calls) == 1


async def test_new_timing_recalculates_without_commands_or_peer_reset(
    hass, window, freezer
):
    runtime, calls = window
    configure(hass, runtime)
    runtime.command(SOURCES, 100)
    await flush(hass)
    middle = runtime.members["cover.middle"].estimated_completion
    freezer.tick(5)
    timings = {
        **runtime.travel_times,
        "cover.left": {"opening_time": 40, "closing_time": 30},
    }
    hass.config_entries.async_update_entry(
        runtime.entry, options={"travel_times": timings}
    )
    runtime.update_options()
    assert runtime.members[
        "cover.left"
    ].estimated_completion == dt_util.utcnow() + timedelta(seconds=40)
    assert runtime.members["cover.middle"].estimated_completion == middle
    assert len(calls) == 3
