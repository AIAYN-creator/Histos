# -*- mode: python ; coding: utf-8 -*-
# Histos desktop app -- PyInstaller build spec. Run from the repo root with:
#   pyinstaller --distpath packaging/dist --workpath packaging/build packaging/histos-gui.spec
#
# onefile (switched from onedir once packaging was proven in Stage 2): a GitHub Release
# asset should be one file to download and double-click, not a zip you have to extract and
# hunt through a 200-file folder for the right exe -- that's exactly the kind of friction
# this app exists to remove. Verified with HISTOS_GUI_SELFTEST=1 against the onefile build
# specifically before this became the shipped default, not assumed from the onedir result.
#
# console=True stays, deliberately, even for this release: nobody (including the person who
# built it) has yet clicked through a live window -- every check so far has been the
# headless selftest or driving the Api directly. A visible console means a first-run crash
# leaves a traceback someone can screenshot and report, instead of the window silently
# vanishing with zero information. Worth revisiting once the app has real runtime history.

import os

# SPECPATH is injected by PyInstaller at spec-execution time (the folder containing this
# file, i.e. packaging/) -- paths built from it stay correct on any machine/checkout,
# unlike a path PyInstaller's own --add-data auto-generation would hardcode.
root = os.path.join(SPECPATH, "..")

a = Analysis(
    [os.path.join(SPECPATH, "gui_entry.py")],
    pathex=[],
    binaries=[],
    datas=[
        (os.path.join(root, "src", "histos", "schema"), "histos/schema"),
        (os.path.join(root, "src", "histos", "templates"), "histos/templates"),
        (os.path.join(root, "src", "histos", "gui", "web"), "histos/gui/web"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    exclude_binaries=False,
    name='histos-gui',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
