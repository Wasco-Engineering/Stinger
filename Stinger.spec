# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


block_cipher = None

hiddenimports = ['encodings', 'pyodbc', 'pyqtgraph']
hiddenimports += collect_submodules('transitions')
hiddenimports += collect_submodules('serial')

binaries = []
for candidate in (
    Path(r"C:\Windows\System32\LabJackM.dll"),
    Path(r"C:\Program Files\LabJack\Drivers\LabJackM.dll"),
    Path(r"C:\Program Files (x86)\LabJack\Drivers\LabJackM.dll"),
):
    if candidate.exists():
        binaries.append((str(candidate), "."))
        break

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=binaries,
    datas=[
        ('stinger_config.yaml', '.'),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    exclude_binaries=False,
    name='Stinger',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app/assets/sps_calibration_stand.ico',
)
