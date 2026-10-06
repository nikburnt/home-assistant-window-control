"""Window Control constants."""

from homeassistant.const import Platform

DOMAIN = "window_control"
PLATFORMS = [Platform.COVER, Platform.BUTTON, Platform.SWITCH]
FEATURES = 15  # Open, close, set position, stop.
