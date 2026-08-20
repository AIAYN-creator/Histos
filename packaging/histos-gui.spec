# -*- mode: python ; coding: utf-8 -*-
# Histos desktop app -- PyInstaller build spec. Run from the repo root with:
#   pyinstaller packaging/histos-gui.spec
#
# console=True and onedir (not onefile) are deliberate for now: this is still the Stage 2
# packaging spike, not a polished release build. A visible console means a crash shows a
# traceback instead of a silently vanishing window; onedir lets you open the output folder
# and confirm histos/schema and histos/templates actually landed where operations.py expects
# them at runtime (see the "Histos desktop app" plan's PyInstaller/importlib.resources
# gotcha -- this is the thing this whole spike exists to prove, verified via
# HISTOS_GUI_SELFTEST=1 against the built exe). Revisit both once packaging is proven and
# this is closer to something a non-technical user installs.

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
    [],
    exclude_binaries=True,
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
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='histos-gui',
)
