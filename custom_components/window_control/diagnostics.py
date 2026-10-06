"""Bounded per-window command trace. Contains no device credentials."""


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    return {
        "configuration": dict(entry.data),
        "verbose_logging": runtime.verbose,
        "members": [
            {
                "source": m.source,
                "role": m.role,
                "phase": m.phase,
                "intent": m.intent,
                "error": m.error,
            }
            for m in runtime.members.values()
        ],
        "recent_commands": list(runtime.trace),
    }
