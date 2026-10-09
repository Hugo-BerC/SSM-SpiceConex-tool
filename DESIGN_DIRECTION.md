# SSM-SpiceConex — Dune visual direction

This POC is intentionally a visual reset. The goal is **Dune-inspired sophistication**, not a generic dark theme with a background image.

## Principles

1. **Mineral dark UI**: charcoal/graphite surfaces are the base; sand and spice are accents.
2. **Texture, not wallpaper**: a procedural dune-ripple texture is composited at low opacity. The supplied artwork is an atmospheric layer only.
3. **Depth over borders**: cards use subtle elevation and translucent surfaces instead of repeated heavy frames.
4. **Operational density**: the interface should feel like a cloud-operations console, not a themed utility.
5. **Dune cues are restrained**: sand ripples, copper/sand accents, Arrakis terminology and geometry; no literal movie UI recreation.
6. **Qt-native control**: combo boxes, dropdowns, tables and navigation are fully styled rather than inheriting Windows/macOS widget chrome.

## Intended full-build direction

- Sidebar navigation replacing the five top tabs.
- Command palette (`Ctrl+K`).
- Custom status indicators and operational badges.
- SVG icon set with a geometric/industrial feel.
- Optional subtle animated grain/ripple layer, disabled automatically on lower-power systems.
- Consistent spacing, typography and interaction states across all five modules.
