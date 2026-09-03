# -*- mode: python ; coding: utf-8 -*-
# Bundles the pipeline, the PowerShell OCR helper, the report template and
# ffmpeg, so the machine running this needs no Python and no ffmpeg install.

import os
import shutil

from PyInstaller.utils.hooks import collect_data_files

datas = [
    ('viewer.html',   '.'),
    ('app.html',      '.'),
    ('ocr_batch.ps1', '.'),
    ('ocr_win.ps1',   '.'),
    ('find_log.ps1',  '.'),
    (r'C:\ffmpeg\ffmpeg.exe',  '.'),
    (r'C:\ffmpeg\ffprobe.exe', '.'),
]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    # pywebview picks its backend at run time, so nothing imports these
    # directly and PyInstaller cannot see them by following imports.
    hiddenimports=['PIL', 'PIL.Image', 'numpy', 'clr',
                   'webview', 'webview.platforms.winforms',
                   'webview.platforms.edgechromium'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'pytest', 'setuptools', 'pip'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='MO2FightLog',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A window, not a console: no black box behind the app.
    console=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    # Named as it ships. This folder is zipped and sent as it stands, so
    # the build makes the finished thing rather than something that has to
    # be copied and renamed first -- that hand step left a second, slowly
    # staler 234MB copy of the whole app sitting beside this one.
    strip=False, upx=False, name='MO2-Fight-Log',
)

# The one file a person is meant to read, put where they will see it: beside
# the exe, not in _internal with the DLLs, which is where a data entry above
# would land it.
shutil.copy2('HOW-TO-USE.txt', os.path.join(DISTPATH, 'MO2-Fight-Log'))
