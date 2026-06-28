# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['gui/app.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('gui/static', 'gui/static'),
        ('src/etalien/proto_compiled', 'etalien/proto_compiled'),
    ],
    hiddenimports=[
        'flask',
        'werkzeug',
        'jinja2',
        'google.protobuf',
        'google._upb._message',
        'requests',
        'etalien.sign',
        'etalien.client',
        'etalien.service',
        'etalien.db',
        'etalien.main',
        'gui.api',
        'gui',
    ],
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
    name='etalien-daily',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='logo/logo.png',
)
