"""SSM-SpiceConex application entry point.

The desktop UI is implemented with PySide6. The former Tkinter implementation
is retained in ``ssm_spiceconex_legacy.py`` only as a migration reference and
is not used by the normal application entry point.
"""

from pyside6_poc.main import main


if __name__ == "__main__":
    main()
