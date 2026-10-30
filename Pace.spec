# -*- mode: python ; coding: utf-8 -*-
# Two executables over one runtime (_internal): Pace.exe (tray/windows) and
# pace-cli.exe (console: terminal and AI agents, MCP server). Built by build.ps1.
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = SPECPATH
datas = [(os.path.join(ROOT, 'assets', 'branding'), 'assets/branding'),
         (os.path.join(ROOT, '.runtime-directml'), 'dml_runtime')]
binaries = []
hiddenimports = ['keyring.backends.Windows']
hiddenimports += collect_submodules('openai')
hiddenimports += collect_submodules('google.genai')
for package in ('faster_whisper', 'ctranslate2', 'av', 'customtkinter', 'keyring', 'win32ctypes', 'numpy',
                'pyaudiowpatch', 'tkinterdnd2', 'aec_audio_processing', 'pyannote.audio', 'pyannote.core',
                'pyannote.pipeline', 'torch', 'torchaudio', 'torchcodec', 'onnx', 'onnxruntime', 'openvino',
                'scipy', 'sklearn', 'huggingface_hub'):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis(
    [os.path.join(ROOT, 'main.py')],
    pathex=[os.path.join(ROOT, 'src')],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

icon = [os.path.join(ROOT, 'assets', 'branding', 'pace.ico')]
common = dict(exclude_binaries=True, debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
              disable_windowed_traceback=False, argv_emulation=False, target_arch=None,
              codesign_identity=None, entitlements_file=None, icon=icon)
gui = EXE(pyz, a.scripts, [], name='Pace', console=False, **common)
cli = EXE(pyz, a.scripts, [], name='pace-cli', console=True, **common)
coll = COLLECT(
    gui,
    cli,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Pace',
)
