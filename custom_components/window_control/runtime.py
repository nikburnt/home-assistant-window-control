"""Independent latest-intent slots, with staggered starts and no command queue."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

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

    def _reached(self, member):
        if not self.available(member.source) or self.moving(member.source):
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
        self._cancel_timer(member, "timer")
        self._cancel_timer(member, "deadline")
        member.intent = None
        member.ready = False
        member.phase = "error"
        member.error = reason
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
        self.publish()

    def _settled(self, member):
        self._cancel_timer(member, "deadline")
        member.phase = "idle"
        member.intent = None
        member.error = None
        self.record(member, "confirmed")

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
        roller_index = 0
        for source in sources:
            member = self.members[source]
            delay = 0
            if member.role == "roller":
                if stagger and not reversing and intent != "stop":
                    delay = roller_index * self.entry.data.get("stagger_seconds", 2)
                roller_index += 1
            if member.intent == intent and member.phase != "error":
                self.record(member, "duplicate_ignored", intent=intent)
                continue
            self._cancel_timer(member, "timer")
            self._cancel_timer(member, "deadline")
            member.revision += 1
            member.intent = intent
            member.context = context
            member.ready = False
            member.error = None
            if not self.available(source):
                self._fail(member, "unavailable")
                continue
            # A stale transport call may still move the motor. Do not skip its correction.
            if intent != "stop" and member.task is None and self._reached(member):
                self._settled(member)
                continue
            member.phase = "pending"
            self.record(member, "requested", intent=intent, delay=delay)
            revision = member.revision

            @callback
            def ready(_now=None, member=member, revision=revision):
                if self._closed or revision != member.revision:
                    return
                member.timer = None
                member.ready = True
                if member.task is None:
                    member.task = self.hass.async_create_background_task(
                        self._dispatch(member),
                        f"window_control {member.source}",
                        eager_start=False,
                    )
                self.publish()

            if delay:
                member.timer = async_call_later(self.hass, delay, ready)
            else:
                ready()
        self.publish()

    async def _dispatch(self, member):
        try:
            while member.ready and member.intent is not None and not self._closed:
                revision, intent = member.revision, member.intent
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
                self.publish()
        finally:
            member.task = None

    async def close(self):
        self._closed = True
        if self._unsub:
            self._unsub()
            self._unsub = None
        tasks = []
        for member in self.members.values():
            self._cancel_timer(member, "timer")
            self._cancel_timer(member, "deadline")
            member.intent = None
            if member.task:
                member.task.cancel()
                tasks.append(member.task)
        await asyncio.gather(*tasks, return_exceptions=True)
        self.listeners.clear()
