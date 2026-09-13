import atexit
import ctypes
import os
import tempfile


LOCK_PATH = os.path.join(tempfile.gettempdir(), 'vry_shownames.lock')

_acquired = False


def _pid_alive(pid):
    if pid is None or not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return exit_code.value == STILL_ACTIVE
        return False
    finally:
        kernel32.CloseHandle(handle)


def _read_lock_pid():
    try:
        with open(LOCK_PATH, 'r', encoding='utf-8') as lock_file:
            content = lock_file.read().strip()
        return int(content) if content else None
    except (OSError, ValueError):
        return None


def acquire_single_instance_lock():
    global _acquired
    if _acquired:
        return True
    existing_pid = _read_lock_pid()
    if existing_pid is not None and existing_pid != os.getpid() and _pid_alive(existing_pid):
        return False
    atexit.register(release_single_instance_lock)
    try:
        with open(LOCK_PATH, 'w', encoding='utf-8') as lock_file:
            lock_file.write(str(os.getpid()))
    except OSError:
        pass
    _acquired = True
    return True


def release_single_instance_lock():
    global _acquired
    _acquired = False
    try:
        if os.path.exists(LOCK_PATH):
            os.remove(LOCK_PATH)
    except OSError:
        pass
