"""Tests hors-ligne du support GPU CUDA.

Couvre ``whisperty.cuda`` (détection GPU/composants, installation opt-in), le repli
gracieux CUDA→CPU et le préchargement des DLL de ``transcriber``, ainsi que le pont
``GuiApi`` (gpu_status / install_gpu).

Aucun appel réseau ni ``pip`` réel : ``subprocess.Popen`` et la détection ctranslate2
sont remplacés par des doublures. 100 % local, déterministe.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if "sounddevice" not in sys.modules:  # secours hors pytest (cf. conftest)
    import tests.conftest  # noqa: F401

from whisperty import cuda


# -- utilitaires de patch (save/restore manuel, lançable hors pytest) ----------
def _save(*specs):
    return [(obj, name, getattr(obj, name)) for obj, name in specs]


def _restore(saved) -> None:
    for obj, name, old in saved:
        setattr(obj, name, old)


def _wait(inst, timeout: float = 5.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if inst.status()["state"] in ("done", "error"):
            return
        time.sleep(0.02)


# =============================================================================
# 1) nvidia_bin_dirs : localisation des dossiers DLL
# =============================================================================
def test_nvidia_bin_dirs() -> None:
    base = Path(tempfile.mkdtemp(prefix="wsp_nvidia_"))
    bin_dir = base / "nvidia" / "cublas" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "cublas64_12.dll").write_bytes(b"")
    empty = base / "nvidia" / "empty" / "bin"
    empty.mkdir(parents=True)  # sans .dll -> ignoré

    saved = _save((importlib.util, "find_spec"))
    try:
        importlib.util.find_spec = lambda name: types.SimpleNamespace(
            submodule_search_locations=[str(base / "nvidia")]
        )
        dirs = cuda.nvidia_bin_dirs()
        assert str(bin_dir) in dirs
        assert str(empty) not in dirs            # dossier bin sans DLL exclu
        importlib.util.find_spec = lambda name: None
        assert cuda.nvidia_bin_dirs() == []      # paquet nvidia absent

        def boom(name):
            raise RuntimeError("introspection cassée")

        importlib.util.find_spec = boom
        assert cuda.nvidia_bin_dirs() == []      # exception -> []
    finally:
        _restore(saved)
    print("[cuda 1] nvidia_bin_dirs : présent / absent / exception  OK")


# =============================================================================
# 2) components_present : cuBLAS ET cuDNN requis
# =============================================================================
def test_components_present() -> None:
    base = Path(tempfile.mkdtemp(prefix="wsp_comp_"))
    cublas = base / "cublas" / "bin"
    cudnn = base / "cudnn" / "bin"
    cublas.mkdir(parents=True)
    cudnn.mkdir(parents=True)

    saved = _save((cuda, "nvidia_bin_dirs"))
    try:
        cuda.nvidia_bin_dirs = lambda: []
        assert cuda.components_present() is False           # rien
        (cublas / "cublas64_12.dll").write_bytes(b"")
        cuda.nvidia_bin_dirs = lambda: [str(cublas)]
        assert cuda.components_present() is False           # cuBLAS seul
        (cudnn / "cudnn64_9.dll").write_bytes(b"")
        cuda.nvidia_bin_dirs = lambda: [str(cublas), str(cudnn)]
        assert cuda.components_present() is True            # cuBLAS + cuDNN
    finally:
        _restore(saved)
    print("[cuda 2] components_present : cuBLAS + cuDNN requis  OK")


# =============================================================================
# 3) device_available : compte de GPU CUDA via ctranslate2
# =============================================================================
def test_device_available() -> None:
    saved_mod = sys.modules.get("ctranslate2")
    try:
        fake = types.ModuleType("ctranslate2")
        fake.get_cuda_device_count = lambda: 1
        sys.modules["ctranslate2"] = fake
        assert cuda.device_available() is True
        fake.get_cuda_device_count = lambda: 0
        assert cuda.device_available() is False
        sys.modules["ctranslate2"] = None  # import -> ImportError -> False
        assert cuda.device_available() is False
    finally:
        if saved_mod is not None:
            sys.modules["ctranslate2"] = saved_mod
        else:
            sys.modules.pop("ctranslate2", None)
    print("[cuda 3] device_available : 1 / 0 / absent  OK")


# =============================================================================
# 4) can_install : possible hors exe figé
# =============================================================================
def test_can_install() -> None:
    had = hasattr(sys, "frozen")
    old = getattr(sys, "frozen", None)
    try:
        if had:
            del sys.frozen
        assert cuda.can_install() is True
        sys.frozen = True
        assert cuda.can_install() is False
    finally:
        if had:
            sys.frozen = old
        elif hasattr(sys, "frozen"):
            del sys.frozen
    print("[cuda 4] can_install : non figé / figé  OK")


# =============================================================================
# 5) _Installer : succès / échec / exception / indisponible
# =============================================================================
def test_installer_flow() -> None:
    class FakePopen:
        def __init__(self, lines, code):
            self.stdout = iter(lines)
            self._code = code

        def wait(self):
            return self._code

    saved = _save((cuda.subprocess, "Popen"), (cuda, "can_install"))
    try:
        cuda.can_install = lambda: True

        # Succès (code 0) : passe par les lignes Collecting/Downloading/Installing.
        cuda.subprocess.Popen = lambda *a, **k: FakePopen(
            ["Collecting nvidia-cublas-cu12", "Downloading x (1 MB)",
             "Installing collected packages", ""], 0)
        inst = cuda._Installer()
        r = inst.start()
        assert r["ok"] is True and r["state"] == "running"
        _wait(inst)
        assert inst.status()["state"] == "done"

        # Échec (code != 0) -> error.
        cuda.subprocess.Popen = lambda *a, **k: FakePopen(["ERROR: boom"], 1)
        inst2 = cuda._Installer()
        inst2.start()
        _wait(inst2)
        assert inst2.status()["state"] == "error"

        # Popen lève -> error (pip introuvable, droits…).
        def boom(*a, **k):
            raise OSError("pip introuvable")

        cuda.subprocess.Popen = boom
        inst3 = cuda._Installer()
        inst3.start()
        _wait(inst3)
        assert inst3.status()["state"] == "error"

        # Indisponible (exe figé) -> ok False, aucun thread lancé.
        cuda.can_install = lambda: False
        inst4 = cuda._Installer()
        r4 = inst4.start()
        assert r4["ok"] is False and "error" in r4
        assert inst4.status()["state"] == "idle"
    finally:
        _restore(saved)
    print("[cuda 5] _Installer : succès / échec / exception / indisponible  OK")


# =============================================================================
# 6) status : agrège détection + état d'installation
# =============================================================================
def test_status() -> None:
    saved = _save((cuda, "device_available"), (cuda, "components_present"),
                  (cuda, "can_install"))
    try:
        cuda.device_available = lambda: True
        cuda.components_present = lambda: False
        cuda.can_install = lambda: True
        s = cuda.status()
        assert s["gpu"] is True and s["components"] is False and s["canInstall"] is True
        assert "install" in s and "message" in s
    finally:
        _restore(saved)
    print("[cuda 6] status : agrégat détection + installation  OK")


# =============================================================================
# 7) Transcriber._effective_device_compute : repli gracieux CUDA->CPU
# =============================================================================
def test_effective_device_compute() -> None:
    from whisperty.config import TranscriptionConfig
    from whisperty.transcriber import Transcriber

    saved = _save((cuda, "device_available"), (cuda, "components_present"))
    try:
        t_cpu = Transcriber(TranscriptionConfig(device="cpu", compute_type="int8"))
        assert t_cpu._effective_device_compute() == ("cpu", "int8")

        cuda.device_available = lambda: True
        cuda.components_present = lambda: True
        t = Transcriber(TranscriptionConfig(device="cuda", compute_type="float16"))
        assert t._effective_device_compute() == ("cuda", "float16")

        cuda.components_present = lambda: False   # composants absents -> repli
        assert t._effective_device_compute() == ("cpu", "int8")
        assert t.cfg.device == "cuda"             # config NON modifiée

        cuda.device_available = lambda: False     # pas de GPU -> repli
        cuda.components_present = lambda: True
        assert t._effective_device_compute() == ("cpu", "int8")
    finally:
        _restore(saved)
    print("[cuda 7] _effective_device_compute : repli gracieux CUDA→CPU  OK")


# =============================================================================
# 8) transcriber._add_cuda_dll_directories : ajout PATH + préchargement
# =============================================================================
def test_add_cuda_dll_directories() -> None:
    from whisperty import transcriber as tr

    base = Path(tempfile.mkdtemp(prefix="wsp_dll_"))
    bindir = base / "cublas" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "fake.dll").write_bytes(b"pas un vrai PE")  # WinDLL échouera (catché)

    saved_flag = tr._cuda_dll_dirs_added
    saved_path = os.environ.get("PATH", "")
    saved = _save((cuda, "nvidia_bin_dirs"))
    try:
        # Rien trouvé -> [] et flag NON marqué (réessai possible après install).
        tr._cuda_dll_dirs_added = False
        cuda.nvidia_bin_dirs = lambda: []
        assert tr._add_cuda_dll_directories() == []
        assert tr._cuda_dll_dirs_added is False

        if os.name == "nt":
            tr._cuda_dll_dirs_added = False
            cuda.nvidia_bin_dirs = lambda: [str(bindir)]
            added = tr._add_cuda_dll_directories()
            assert added == [str(bindir)]
            assert tr._cuda_dll_dirs_added is True
            assert str(bindir) in os.environ["PATH"]
            # Idempotent : 2e appel -> [] (déjà fait).
            assert tr._add_cuda_dll_directories() == []
    finally:
        _restore(saved)
        tr._cuda_dll_dirs_added = saved_flag
        os.environ["PATH"] = saved_path
    print("[cuda 8] _add_cuda_dll_directories : rien / ajout+PATH / idempotent  OK")


# =============================================================================
# 9) GuiApi : pont gpu_status / install_gpu (+ robustesse)
# =============================================================================
def test_gui_gpu_bridge() -> None:
    from whisperty.gui import GuiApi

    api = GuiApi(None)  # gpu_status/install_gpu n'utilisent pas self._app
    saved = _save((cuda, "status"), (cuda, "start_install"))
    try:
        cuda.status = lambda: {"gpu": True, "components": False, "canInstall": True,
                               "install": "idle", "message": ""}
        assert api.gpu_status()["components"] is False
        cuda.start_install = lambda: {"ok": True, "state": "running"}
        assert api.install_gpu() == {"ok": True, "state": "running"}

        def boom():
            raise RuntimeError("indisponible")

        cuda.status = boom
        s = api.gpu_status()
        assert s["gpu"] is False and s["install"] == "idle"  # repli robuste

        cuda.start_install = boom
        assert api.install_gpu()["ok"] is False
    finally:
        _restore(saved)
    print("[cuda 9] GuiApi.gpu_status / install_gpu (+ robustesse)  OK")


def _run_all() -> None:
    test_nvidia_bin_dirs()
    test_components_present()
    test_device_available()
    test_can_install()
    test_installer_flow()
    test_status()
    test_effective_device_compute()
    test_add_cuda_dll_directories()
    test_gui_gpu_bridge()
    print("\nTOUS LES TESTS CUDA PASSENT")


if __name__ == "__main__":
    _run_all()
