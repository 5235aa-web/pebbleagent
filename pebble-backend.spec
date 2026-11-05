# -*- mode: python ; coding: utf-8 -*-

import os

# ripgrep backs the grep tool. The binary is git-ignored, so a checkout without
# it still packages; the tool then falls back to PATH at runtime.
_rg_binary = 'vendor/ripgrep/rg.exe'

a = Analysis(
    ['run_backend.py'],
    pathex=[],
    binaries=[(_rg_binary, 'vendor/ripgrep')] if os.path.exists(_rg_binary) else [],
    datas=[('skills', 'skills'), ('agent_core\\agents', 'agent_core\\agents'), ('llm_providers.json', '.'), ('agent_model_config.json', '.'), ('model_pricing.json', '.'), ('cn_holidays.json', '.'), ('path_whitelist.json', '.'), ('permission_rules.json', '.'), ('usernote.json', '.'), ('.mcp.json', '.'), ('mcp_install.json', '.')],
    hiddenimports=['cryptography', 'cryptography.hazmat.primitives.asymmetric.rsa', 'segno'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'sounddevice'],
    noarchive=False,
    optimize=0,
)

# Neither numpy nor sounddevice loads at runtime: numpy existed for the OCR
# branch that was removed, and sounddevice was never imported at all (only a
# build-time hook collected its portaudio DLLs). Excluding the modules is not
# enough on Windows, where numpy ships its BLAS as plain binaries under
# numpy.libs, so the collected file lists are filtered as well.
def _unwanted(name: str) -> bool:
    n = name.replace('\\', '/').lower()
    return n.startswith(('numpy', '_sounddevice_data'))


a.datas = [d for d in a.datas if not _unwanted(d[0])]
a.binaries = [b for b in a.binaries if not _unwanted(b[0])]

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
