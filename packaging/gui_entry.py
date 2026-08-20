"""PyInstaller entry point for the Histos desktop app.

Deliberately lives outside src/histos/ and uses only an absolute import. PyInstaller's
Analysis step effectively runs the entry script as a bare __main__, which breaks relative
imports (src/histos/gui/app.py uses `from .. import canvas, operations`) if that file is
targeted directly. Importing the already-installed package by its full dotted path here
sidesteps that -- normal Python import resolution, no ambiguity for PyInstaller to get
wrong.
"""
from histos.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
