# Window Control

A Home Assistant HACS integration coordinating ordered roller shades and an
optional curtain on one window. All scheduling runs in Home Assistant, not in
the browser. Use the companion [Window Control Card](https://github.com/nikburnt/ha-window-control-card).

## Installation

Add `nikburnt/home-assistant-window-control` as a custom Integration repository
in HACS, download, and restart Home Assistant. Add **Window Control** under
Settings > Devices & services. Select roller shades in left-to-right order and
an optional curtain. Existing sources must support open, close, stop and position.
Settings can be changed using Reconfigure. The default start interval is two
seconds; movement confirmation times out after 90 seconds.

## Entities and automations

One device exposes:

- An individual `cover` for each roller shade and for the optional curtain.
- A **Roller Shades** cover for commands affecting only the rollers.
- **Open All**, **Close All**, **Stop All** buttons for the whole window.
- A persistent, per-window **Verbose Logging** switch.

Automations use normal `cover.open_cover`, `cover.close_cover`,
`cover.set_cover_position`, `cover.stop_cover`, and `button.press` actions.
Target the Window Control entities, not the physical sources, so individual
overrides cancel that member's pending start. Physical source states are always
mirrored. Direct commands to a physical source bypass the scheduler.

## Command contract

- A normal group start dispatches rollers at 0, 2, 4... seconds. The curtain
  starts at zero for whole-window commands. The interval is configurable.
- An individual command replaces only that member's intent. Others continue.
- Repeating an active target is a no-op, preserving the original stagger.
- Stop cancels pending starts and sends stop to every targeted member.
- A different target while a group is active replaces pending targets without
  a new stagger. There is no FIFO command queue.
- An already in-flight device service cannot be recalled. Each member has one
  transport call and one replaceable latest intent; other members never wait
  for it. A call has an eight-second transport timeout. This cannot override
  transport/firmware latency or guarantee cancellation of packets already sent.
- Device failures are isolated. Recovery never replays old commands. Repeat
  the group command to retry a failed member; active matching members continue.
- Startup, reload and unload do not send movement commands. Pending starts and
  intents are not persisted. A motor already moving may continue on its own.
- Position comes from the source, not a timer or optimistic animation. The
  roller group reports a percentage only when all rollers agree. Curtain and
  roller positions are never averaged together.

Individual entities expose `phase`, `target_position`, and `last_error`.
Download diagnostics for the last 50 scheduling/dispatch/confirmation events.
Verbose logging adds these events to the Home Assistant log at INFO level.
The integration does not add entities to Recorder/InfluxDB allowlists.

## Travel time estimates

Use **Configure** on the integration entry, select a cover, and enter its full
opening and closing times in seconds. Each direction is independent; zero
(the default) disables its estimate. Repeat for the other covers. Settings
apply without reloading the integration or sending commands.

Individual covers expose `opening_time`, `closing_time`, and an optional UTC
`estimated_completion` timestamp. The estimate starts at command dispatch,
uses the remaining fraction of travel from the reported position, and is
corrected when that position changes. It is approximate: motor speed, packet
delay and position reporting can vary. It does not reschedule the cascade,
replace `current_position`, or change the confirmation timeout. A duplicate
command or unrelated state update does not restart the countdown.

Stop, a replacement target, failure, unavailability, and unload clear the old
estimate. No estimate is restored after restart or inferred for direct source
commands. Reaching the estimated time is not confirmation; the card continues
waiting for a device report. No periodic HA state updates are created for the
countdown; the companion card updates its display locally.

## Development

Python 3.14.2 or later. Install `requirements-test.txt`, then run `pytest -q`
and `ruff check custom_components tests`. Tests use simulated covers only.
No Home Assistant credentials or installation-specific device IDs belong here.
