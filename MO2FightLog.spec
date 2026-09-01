# -*- mode: python ; coding: utf-8 -*-
# Bundles the pipeline, the PowerShell OCR helper, the report template and
# ffmpeg, so the machine running this needs no Python and no ffmpeg install.

datas = [
    ('viewer.html',   '.'),
    ('ocr_batch.ps1', '.'),
    ('ocr_win.ps1',   '.'),
    ('find_log.ps1',  '.'),
    (r'C:\ffmpeg\ffmpeg.exe',  '.'),
    (r'C:\ffmpeg\ffprobe.exe', '.'),
]

a = Analysis(
    ['mo2fightlog.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['PIL', 'PIL.Image', 'numpy'],
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
    console=True,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, name='MO2FightLog',
)
