# Window Control

- **Window**: a named set of independent shading layers at one physical window.
- **Roller**: one vertically moving shade, ordered left to right within a window.
- **Curtain**: an optional horizontally moving layer independent of the rollers.
- **Whole-window command**: an open, close or stop request affecting both layers.
- **Roller command**: a request affecting the roller layer only.
- **Intent**: the latest requested target or stop for one member.
- **Source**: the existing physical cover entity supplied by another integration.
- **Public cover**: the Window Control entity that accepts scheduled commands.

Window Control coordinates distinct physical devices. Unified Device combines
multiple integration representations of the same physical device; it is not a
window group.
