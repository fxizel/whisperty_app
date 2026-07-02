# -*- mode: python ; coding: utf-8 -*-
# PyInstaller — Whisperty (build de déploiement)
#
#   pyinstaller whisperty.spec
#   (ou, recommandé : scripts/build.ps1 qui assemble aussi config + dictionnaire + modèle)
#
# Produit un dossier autonome  dist/whisperty/  (mode « onedir ») contenant
# whisperty.exe et toutes ses dépendances natives.
#
# Pourquoi onedir (et non onefile) :
#   - démarrage immédiat (le onefile se décompresse dans un dossier temporaire à
#     CHAQUE lancement → lent, pénalisant pour une app qui démarre avec Windows) ;
#   - c'est exactement la structure que l'installeur (Inno Setup) recopie.
#
# Ce qui N'EST PAS embarqué dans l'exe (déposé À CÔTÉ par l'installeur, éditable) :
#   - config.yaml, dictionary.txt  → réglages utilisateur ;
#   - le modèle Whisper             → volumineux ; bundlé dans models/ par build.ps1
#                                      si demandé (sinon téléchargé au 1er lancement).
# Ce qui EST embarqué (requis à l'exécution) :
#   - whisperty/web/                → assets de l'interface (gui.web_dir → sys._MEIPASS) ;
#   - assets faster-whisper (VAD Silero), DLL ctranslate2/PyAV/OpenMP, soundcard,
#     et la pile pywebview (.NET WebView2 interop via pythonnet/clr_loader).

import os

from PyInstaller.utils.hooks import collect_all

# Répertoire du .spec (≠ CWD selon l'invocation).
ROOT = os.path.abspath(SPECPATH)  # noqa: F821  (SPECPATH injecté par PyInstaller)

datas, binaries, hiddenimports = [], [], []

# Paquets dont il faut récupérer données + binaires natifs + sous-modules cachés.
#   audio/ASR : faster_whisper (VAD Silero), ctranslate2 (DLL), soxr, sounddevice (PortAudio)
#   loopback  : soundcard (cffi WASAPI)
#   tray/input: pystray, pynput
#   fenêtre   : webview (pywebview + DLL WebView2 interop), pythonnet/clr_loader (.NET)
for package in (
    "faster_whisper",
    "ctranslate2",
    "soxr",
    "sounddevice",
    "soundcard",
    "pystray",
    "pynput",
    "webview",
    "pythonnet",
    "clr_loader",
):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception as exc:  # paquet optionnel absent → on continue (repli tray seul)
        print(f"[whisperty.spec] collect_all({package!r}) ignoré : {exc}")
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Assets de l'interface (CRITIQUE : sans eux, la fenêtre WebView2 est vide en build figé).
datas += [(os.path.join(ROOT, "whisperty", "web"), os.path.join("whisperty", "web"))]

# Backends pywebview chargés dynamiquement (introspection → PyInstaller ne les voit pas).
hiddenimports += ["webview.platforms.edgechromium", "webview.platforms.winforms", "clr"]

icon = os.path.join(ROOT, "installer", "whisperty.ico")
icon = icon if os.path.isfile(icon) else None

version_file = os.path.join(ROOT, "build", "version_info.txt")
version_arg = version_file if os.path.isfile(version_file) else None

a = Analysis(
    [os.path.join(ROOT, "whisperty_launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "pytest", "_pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir : les binaires sont collectés par COLLECT
    name="whisperty",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX corrompt les DLL natives ctranslate2/PyAV/OpenMP
    console=False,           # application de tray/fenêtre : pas de console
    disable_windowed_traceback=False,
    icon=icon,
    version=version_arg,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="whisperty",
)
