"""Independent latest-intent slots, with staggered starts and no command queue."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.core import Context, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)
TRANSPORT_TIMEOUT = 8


@dataclass
class Member:
    source: str
    role: str
    label: str
    revision: int = 0
    intent: int | str | None = None
    context: Context | None = None
    ready: bool = False
    phase: str = "idle"
    error: str | None = None
    timer: Callable | None = None
    deadline: Callable | None = None
    task: asyncio.Task | None = None
    response: asyncio.Event | None = None
    inflight_response: asyncio.Event | None = None
    estimated_completion: datetime | None = None
    estimate_position: int | None = None


class WindowRuntime:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.members = {
            source: Member(source, "roller", f"Roller {index + 1}")
            for index, source in enumerate(entry.data["rollers"])
        }
        if source := entry.data.get("curtain"):
            self.members[source] = Member(source, "curtain", "Curtain")
        self.listeners = set()
        self.trace = deque(maxlen=50)
        self._unsub = None
        self._closed = False
        self._cascades: set[asyncio.Task] = set()
        self.travel_times = entry.options.get("travel_times", {})

    @property
    def verbose(self):
        return self.entry.options.get("verbose_logging", False)

    def start(self):
        self._unsub = async_track_state_change_event(
            self.hass, list(self.members), self._source_changed
        )

    def state(self, source):
        return self.hass.states.get(source)

    def available(self, source):
        state = self.state(source)
        return state is not None and state.state != "unavailable"

    def position(self, source):
        state = self.state(source)
        value = state.attributes.get("current_position") if state else None
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0 <= value <= 100
        ):
            return round(value)
        return None

    def moving(self, source):
        state = self.state(source)
        return state is not None and state.state in ("opening", "closing")

    def _update_estimate(self, member):
        member.estimated_completion = None
        member.estimate_position = self.position(member.source)
        state = self.state(member.source)
        if (
            member.phase not in ("sending", "awaiting_confirmation")
            or not isinstance(member.intent, int)
            or member.estimate_position is None
            or state is None
            or state.state in ("unknown", "unavailable")
        ):
            member.estimate_position = None
            return
        distance = member.intent - member.estimate_position
        direction = (
            "opening_time"
            if distance > 0 or (distance == 0 and state.state == "opening")
            else "closing_time"
        )
        seconds = self.travel_times.get(member.source, {}).get(direction, 0)
        if seconds > 0:
            member.estimated_completion = dt_util.utcnow() + timedelta(
                seconds=abs(distance) * seconds / 100
            )

    @callback
    def update_options(self):
        previous = self.travel_times
        self.travel_times = self.entry.options.get("travel_times", {})
        for member in self.members.values():
            if previous.get(member.source) != self.travel_times.get(member.source):
                self._update_estimate(member)
        self.publish()

    def _reached(self, member):
        if (
            not self.available(member.source)
            or self.state(member.source).state == "unknown"
            or self.moving(member.source)
        ):
            return False
        if member.intent == "stop":
            state = self.state(member.source)
            return state is not None and state.state in ("open", "closed")
        return self.position(member.source) == member.intent

    @callback
    def publish(self):
        for listener in tuple(self.listeners):
            listener()

    def record(self, member, action, **details):
        item = {
            "time": dt_util.utcnow().isoformat(),
            "source": member.source,
            "action": action,
            "revision": member.revision,
            "context_id": member.context.id if member.context else None,
            **details,
        }
        self.trace.append(item)
        if self.verbose:
            _LOGGER.info("%s: %s", self.entry.title, item)

    @staticmethod
    def _cancel_timer(member, attribute):
        if cancel := getattr(member, attribute):
            cancel()
            setattr(member, attribute, None)

    def _fail(self, member, reason):
        self._release_pending(member)
        self._cancel_timer(member, "timer")
        self._cancel_timer(member, "deadline")
        member.intent = None
        member.ready = False
        member.phase = "error"
        member.error = reason
        member.estimated_completion = None
        self.record(member, "failed", reason=reason)

    @callback
    def _source_changed(self, event):
        member = self.members[event.data["entity_id"]]
        if member.intent is not None:
            if not self.available(member.source):
                member.revision += 1
                self._fail(member, "unavailable")
            elif member.phase == "awaiting_confirmation" and self._reached(member):
                self._settled(member)
            elif (
                self.position(member.source) != member.estimate_position
                or self.state(member.source).state == "unknown"
                or (
                    member.estimated_completion is None
                    and self.state(member.source).state in ("opening", "closing")
                )
            ):
                self._update_estimate(member)
        self.publish()

    def _settled(self, member):
        self._cancel_timer(member, "deadline")
        member.phase = "idle"
        member.intent = None
        member.error = None
        member.estimated_completion = None
        self.record(member, "confirmed")

    @staticmethod
    def _release_pending(member):
        # A superseded in-flight call releases its cascade only when it returns.
        if (
            member.response is not None
            and member.response is not member.inflight_response
        ):
            member.response.set()

    def _ready(self, member):
        member.ready = True
        if member.task is None:
            member.task = self.hass.async_create_background_task(
                self._dispatch(member),
                f"window_control {member.source}",
                eager_start=False,
            )

    async def _cascade(self, steps):
        previous_response = None
        for member, revision, response, needs_start in steps:
            if self._closed or revision != member.revision:
                continue
            if needs_start:
                delay = 0
                if previous_response is not None:
                    delay = (
                        self.entry.data.get("stagger_seconds", 2)
                        - (dt_util.utcnow() - previous_response).total_seconds()
                    )
                if delay > 0:
                    wake = asyncio.Event()

                    @callback
                    def elapsed(_now, member=member, wake=wake):
                        member.timer = None
                        wake.set()

                    cancel = async_call_later(self.hass, delay, elapsed)

                    @callback
                    def cancel_wait(cancel=cancel, wake=wake):
                        cancel()
                        wake.set()

                    member.timer = cancel_wait
                    await wake.wait()
                if self._closed or revision != member.revision:
                    continue
                self._ready(member)
                self.publish()
            await response.wait()
            previous_response = dt_util.utcnow()

    @callback
    def command(self, sources, intent, context=None, stagger=False):
        """Replace only the selected members. Never wait for a motor here."""
        if self._closed:
            return
        sources = tuple(sources)
        if intent != "stop" and (
            isinstance(intent, bool)
            or not isinstance(intent, int)
            or not 0 <= intent <= 100
        ):
            raise ValueError("Position must be an integer from 0 to 100")
        if any(source not in self.members for source in sources):
            raise ValueError("Cover does not belong to this window")
        if stagger and self.entry.data.get("reverse_stagger", False):
            rollers = [s for s in sources if self.members[s].role == "roller"]
            curtains = [s for s in sources if self.members[s].role == "curtain"]
            sources = (*reversed(rollers), *curtains)
        reversing = intent != "stop" and any(
            (self.members[source].intent not in (None, "stop", intent))
            or (
                self.moving(source)
                and (
                    (
                        self.state(source).state == "opening"
                        and intent < (self.position(source) or 0)
                    )
                    or (
                        self.state(source).state == "closing"
                        and intent > (self.position(source) or 0)
                    )
                )
            )
            for source in sources
        )
        cascade = stagger and not reversing and intent != "stop"
        steps = []
        for source in sources:
            member = self.members[source]
            self._release_pending(member)
            self._cancel_timer(member, "timer")
            self._cancel_timer(member, "deadline")
            member.revision += 1
            member.intent = intent
            member.context = context
            member.ready = False
            member.error = None
            member.estimated_completion = None
            if not self.available(source):
                self._fail(member, "unavailable")
                continue
            member.phase = "pending"
            member.response = asyncio.Event()
            wait_for_previous = cascade and member.role == "roller" and bool(steps)
            self.record(
                member, "requested", intent=intent, wait_for_previous=wait_for_previous
            )
            if cascade and member.role == "roller":
                steps.append(
                    (member, member.revision, member.response, wait_for_previous)
                )
            if not wait_for_previous:
                self._ready(member)
        if len(steps) > 1:
            task = self.hass.async_create_background_task(
                self._cascade(steps), "window_control cascade", eager_start=False
            )
            self._cascades.add(task)
            task.add_done_callback(self._cascades.discard)
        self.publish()

    async def _dispatch(self, member):
        try:
            while member.ready and member.intent is not None and not self._closed:
                revision, intent, response = (
                    member.revision,
                    member.intent,
                    member.response,
                )
                member.ready = False
                if not self.available(member.source):
                    self._fail(member, "unavailable")
                    break
                data = {"entity_id": member.source}
                if intent == "stop":
                    service = "stop_cover"
                elif intent in (0, 100):
                    service = "open_cover" if intent else "close_cover"
                else:
                    service = "set_cover_position"
                    data["position"] = intent
                member.phase = "sending"
                member.inflight_response = response
                self._update_estimate(member)
                self.record(member, "dispatch", service=service, intent=intent)
                self.publish()
                try:
                    async with asyncio.timeout(TRANSPORT_TIMEOUT):
                        await self.hass.services.async_call(
                            "cover",
                            service,
                            data,
                            blocking=True,
                            context=member.context,
                        )
                except Exception as error:
                    _LOGGER.exception("Cover service failed for %s", member.source)
                    if revision == member.revision:
                        self._fail(member, type(error).__name__)
                else:
                    if revision == member.revision and member.intent is not None:
                        self.record(member, "response", intent=intent)
                        if self._reached(member):
                            self._settled(member)
                        else:
                            member.phase = "awaiting_confirmation"

                            @callback
                            def expired(_now: datetime, revision=revision):
                                if (
                                    revision == member.revision
                                    and member.intent is not None
                                ):
                                    self._fail(member, "confirmation_timeout")
                                    self.publish()

                            member.deadline = async_call_later(
                                self.hass,
                                self.entry.data.get("travel_timeout", 90),
                                expired,
                            )
                finally:
                    response.set()
                    member.inflight_response = None
                self.publish()
        finally:
            member.task = None

    async def close(self):
        self._closed = True
        if self._unsub:
            self._unsub()
            self._unsub = None
        tasks = list(self._cascades)
        for task in tasks:
            task.cancel()
        for member in self.members.values():
            self._cancel_timer(member, "timer")
            self._cancel_timer(member, "deadline")
            member.intent = None
            member.estimated_completion = None
            if member.task:
                member.task.cancel()
                tasks.append(member.task)
        await asyncio.gather(*tasks, return_exceptions=True)
        self.listeners.clear()
