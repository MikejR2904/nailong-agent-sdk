# Copyright (c) 2026 David Michael Indraputra

"""Freeze and thaw running commands in place, so a pause does not restart them.

A frozen process keeps its memory, open files and position; nothing is replayed on thaw. POSIX
sends SIGSTOP and SIGCONT to the command's process group. Windows suspends every process of the
command's tree with the ``NtSuspendProcess`` call, which is not a documented Win32 API but is what
process tools use for this. A command's own time limit does not run while it is frozen.
"""

from __future__ import annotations

import os
import signal
import threading
from dataclasses import dataclass, field

from ..foundations.errors import AgentSdkError


@dataclass
class FrozenCommand:
    pid: int
    label: str
    foreground: bool
    frozen: bool = False
    pids: list[int] = field(default_factory=list)


class CommandFreezer:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[int, FrozenCommand] = {}

    def register(self, pid: int, label: str, *, foreground: bool) -> FrozenCommand:
        entry = FrozenCommand(pid, label, foreground)
        with self._lock:
            self._entries[pid] = entry
        return entry

    def unregister(self, pid: int) -> None:
        with self._lock:
            self._entries.pop(pid, None)

    def entries(self) -> list[FrozenCommand]:
        with self._lock:
            return list(self._entries.values())

    def freeze(self, *, foreground_only: bool = True, label: str | None = None) -> list[str]:
        with self._lock:
            chosen = [
                entry
                for entry in self._entries.values()
                if not entry.frozen
                and (label is None or entry.label == label)
                and (label is not None or not foreground_only or entry.foreground)
            ]
            for entry in chosen:
                _suspend(entry)
                entry.frozen = True
            return [entry.label for entry in chosen]

    def thaw_pid(self, pid: int) -> None:
        with self._lock:
            entry = self._entries.get(pid)
            if entry is not None and entry.frozen:
                _resume(entry)
                entry.frozen = False

    def thaw(self, *, label: str | None = None) -> list[str]:
        with self._lock:
            chosen = [
                entry
                for entry in self._entries.values()
                if entry.frozen and (label is None or entry.label == label)
            ]
            for entry in chosen:
                _resume(entry)
                entry.frozen = False
            return [entry.label for entry in chosen]


FREEZER = CommandFreezer()


def _suspend(entry: FrozenCommand) -> None:
    if os.name == "nt":
        suspended: list[int] = []
        try:
            for pid in _windows_tree(entry.pid):
                _windows_call("NtSuspendProcess", pid, entry.label)
                suspended.append(pid)
        except AgentSdkError:
            for pid in suspended:
                _windows_call("NtResumeProcess", pid, entry.label)
            raise
        entry.pids = suspended
        return
    try:
        os.killpg(entry.pid, signal.SIGSTOP)
    except OSError as error:
        raise _failure("freeze", entry.label, error) from error


def _resume(entry: FrozenCommand) -> None:
    if os.name == "nt":
        for pid in entry.pids:
            _windows_call("NtResumeProcess", pid, entry.label)
        entry.pids = []
        return
    try:
        os.killpg(entry.pid, signal.SIGCONT)
    except OSError as error:
        raise _failure("thaw", entry.label, error) from error


def _failure(action: str, label: str, error: BaseException) -> AgentSdkError:
    return AgentSdkError(
        "COMMAND_FREEZE_FAILED",
        f'Could not {action} the command "{label}": {type(error).__name__}: {error}',
        {"command": label, "action": action},
    )


def _windows_tree(root: int) -> list[int]:
    import ctypes
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
    if snapshot in (None, wintypes.HANDLE(-1).value):
        raise _failure("freeze", str(root), OSError("the process list could not be read"))
    children: dict[int, list[int]] = {}
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    tree, queue = [], [root]
    while queue:
        pid = queue.pop()
        if pid not in tree:
            tree.append(pid)
            queue.extend(children.get(pid, []))
    return tree


def _windows_call(function: str, pid: int, label: str) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x0800, False, pid)
    if not handle:
        raise _failure(
            function,
            label,
            OSError(f"process {pid} could not be opened (error {ctypes.get_last_error()})"),
        )
    try:
        status = getattr(ctypes.WinDLL("ntdll"), function)(handle)
    finally:
        kernel.CloseHandle(handle)
    if status != 0:
        raise _failure(
            function, label, OSError(f"{function} on process {pid} returned {status:#x}")
        )
