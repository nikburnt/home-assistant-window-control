"""Bounded per-window command trace. Contains no device credentials."""


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    return {
        "configuration": dict(entry.data),
        "verbose_logging": runtime.verbose,
        "travel_times": dict(runtime.travel_times),
        "members": [
            {
                "source": m.source,
                "role": m.role,
                "phase": m.phase,
                "intent": m.intent,
                "error": m.error,
                "estimated_completion": m.estimated_completion.isoformat()
                if m.estimated_completion
                else None,
            }
            for m in runtime.members.values()
        ],
        "recent_commands": list(runtime.trace),
    }
