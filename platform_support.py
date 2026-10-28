"""Small native desktop operations shared by the Windows and macOS app."""
import atexit
import importlib
import os
import shutil
import subprocess
import sys
import threading


def windows_updates_supported():
    """This fork has no compatible automatic update channel yet.

    The inherited Google Drive feed belongs to the original distribution. Its
    Windows executable would replace this fork and remove the fixes, even when
    an old config.json still contains auto_update=true.
    """
    return False


def load_webview():
    """Load GUI dependencies only for a graphical launch, never for --run."""
    try:
        return importlib.import_module("webview")
    except Exception as exc:
        if sys.platform == "darwin":
            hint = "Установите зависимости: python3 -m pip install -r requirements.txt (pywebview и PyObjC)."
        elif sys.platform == "win32":
            hint = "Установите зависимости, .NET Framework 4.8 и Microsoft Edge WebView2 Runtime."
        else:
            hint = "Установите pywebview и его GTK/Qt backend; командный режим --run не требует GUI."
        message = f"Не удалось загрузить окно Kadro.Gen. {hint}\nПричина: {exc}"
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "Kadro.Gen", 0x10)
        raise RuntimeError(message) from exc


def open_path(path):
    """Open a local file or directory using the platform default application."""
    path = os.path.abspath(os.fspath(path))
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def reveal_path(path):
    path = os.path.abspath(os.fspath(path))
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", path])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        open_path(os.path.dirname(path))


def copy_text(text):
    """Copy Unicode text; return False when no clipboard backend is available."""
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=10)
        return True
    if sys.platform != "win32":
        command = (["wl-copy"] if shutil.which("wl-copy") else
                   ["xclip", "-selection", "clipboard"] if shutil.which("xclip") else None)
        if command is None:
            return False
        subprocess.run(command, input=text.encode("utf-8"), check=True, timeout=10)
        return True
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not u32.OpenClipboard(None):
        return False
    handle = None
    try:
        handle = k32.GlobalAlloc(0x0002, len(data))
        if not handle:
            return False
        pointer = k32.GlobalLock(handle)
        if not pointer:
            return False
        ctypes.memmove(pointer, data, len(data))
        k32.GlobalUnlock(handle)
        u32.EmptyClipboard()
        if not u32.SetClipboardData(13, handle):
            return False
        handle = None  # The system now owns the allocation.
        return True
    finally:
        if handle:
            k32.GlobalFree(handle)
        u32.CloseClipboard()


_awake_process = None
_awake_lock = threading.Lock()


def set_keep_awake(on):
    global _awake_process
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if on else 0x80000000)
    elif sys.platform == "darwin":
        with _awake_lock:
            if on and (_awake_process is None or _awake_process.poll() is not None):
                _awake_process = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
            elif not on and _awake_process is not None:
                _awake_process.terminate()
                try:
                    _awake_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _awake_process.kill()
                    _awake_process.wait()
                _awake_process = None


_instance_file = None
_instance_mutex = None


def acquire_instance_lock(directory):
    """Hold an OS lock until exit; a crashed process releases it automatically."""
    global _instance_file, _instance_mutex
    if sys.platform == "win32":
        import ctypes
        k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
        k32.CreateMutexW.restype = ctypes.c_void_p
        _instance_mutex = k32.CreateMutexW(None, False, "KadroGen.SingleInstance")
        if not _instance_mutex:
            raise OSError("Не удалось создать блокировку Kadro.Gen")
        if k32.GetLastError() != 183:
            return True
        u32.FindWindowW.restype = ctypes.c_void_p
        u32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        hwnd = u32.FindWindowW(None, "Kadro.Gen")
        if hwnd:
            u32.ShowWindow(hwnd, 9)
            u32.SetForegroundWindow(hwnd)
        return False
    if _instance_file is not None:
        return True
    import fcntl
    handle = open(os.path.join(directory, ".kadrogen.lock"), "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return False
    _instance_file = handle
    return True


def _cleanup():
    if _awake_process is not None:
        set_keep_awake(False)


atexit.register(_cleanup)
