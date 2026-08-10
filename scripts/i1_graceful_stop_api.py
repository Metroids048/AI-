"""I-1 step 2: send CTRL_BREAK to the API/scheduler console process for a graceful stop.

uvicorn handles SIGBREAK on Windows, which runs the FastAPI lifespan ``finally``
block and therefore ``await scheduler.stop()`` — draining in-flight cycles with a
5s timeout instead of severing them mid-transaction.

Runs AttachConsole/FreeConsole in this throwaway process so the caller's console
is never affected. Does not touch the database.
"""

from __future__ import annotations

import ctypes
import sys
import time

CTRL_BREAK_EVENT = 1
ATTACH_PARENT_PROCESS = -1


def _alive(pid: int) -> bool:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k = ctypes.windll.kernel32
    handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not k.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k.CloseHandle(handle)


def graceful_stop(pid: int, *, wait_seconds: int = 30) -> int:
    k = ctypes.windll.kernel32
    if not _alive(pid):
        print(f"PID {pid} is not running")
        return 0

    k.FreeConsole()
    if not k.AttachConsole(pid):
        err = ctypes.get_last_error()
        print(f"AttachConsole({pid}) failed, winerr={err}")
        return 2

    # Do not let this helper die from the event it is about to raise.
    k.SetConsoleCtrlHandler(None, True)
    ok = k.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, 0)
    err = ctypes.get_last_error() if not ok else 0
    k.FreeConsole()

    if not ok:
        print(f"GenerateConsoleCtrlEvent failed, winerr={err}")
        return 3

    print(f"CTRL_BREAK sent to PID {pid}; waiting up to {wait_seconds}s for graceful exit")
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if not _alive(pid):
            print("GRACEFUL_EXIT=true")
            return 0
        time.sleep(1)
    print("GRACEFUL_EXIT=false")
    return 4


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: i1_graceful_stop_api.py <pid> [wait_seconds]")
        raise SystemExit(64)
    target = int(sys.argv[1])
    wait = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    raise SystemExit(graceful_stop(target, wait_seconds=wait))
