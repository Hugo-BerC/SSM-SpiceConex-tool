# PySide6 Dune Visual POC

This iteration deliberately changes the visual direction rather than merely replacing Tkinter widgets with Qt widgets.

## Run visual demo

```bash
python -m pip install -r requirements.txt
python -m pyside6_poc.main --demo
```

The demo uses no AWS calls and renders sample instances.

## Design direction

- dark mineral surfaces rather than brown panels
- subtle procedural sand/ripple texture
- original Dune artwork used only as a low-opacity atmospheric layer
- sidebar navigation instead of classic tabs
- restrained sand/spice accent system
- custom dark combo-box dropdowns
- soft depth/shadows instead of heavy borders
- compact operational metrics and a modern table
