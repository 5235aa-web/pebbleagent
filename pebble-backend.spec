# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run_backend.py'],
    pathex=[],
    binaries=[],
    datas=[('skills', 'skills'), ('multi_agents\\agents', 'multi_agents\\agents'), ('knowledgesource', 'knowledgesource'), ('llm_providers.json', '.'), ('agent_model_config.json', '.'), ('model_pricing.json', '.'), ('cn_holidays.json', '.'), ('path_whitelist.json', '.'), ('permission_rules.json', '.'), ('usernote.json', '.'), ('.mcp.json', '.'), ('mcp_install.json', '.')],
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
    name='pebble-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
