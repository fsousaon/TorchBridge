from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from typing import Iterable

from .models import Rect


IS_WINDOWS = os.name == "nt"


class Win32Unavailable(RuntimeError):
    pass


if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    winmm = ctypes.WinDLL("winmm", use_last_error=True)

    ULONG_PTR = wintypes.WPARAM

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    user32.MapVirtualKeyW.restype = wintypes.UINT
    user32.GetForegroundWindow.argtypes = ()
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.IsWindow.argtypes = (wintypes.HWND,)
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    user32.GetSystemMetrics.restype = ctypes.c_int
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    user32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
    user32.MessageBoxW.restype = ctypes.c_int

    if hasattr(user32, "GetWindowLongPtrW"):
        user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    else:
        user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.GetWindowLongW.restype = wintypes.LONG
        user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
        user32.SetWindowLongW.restype = wintypes.LONG


VK_NAMES = {
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "TAB": 0x09,
    "SHIFT": 0x10,
    "ALT": 0x12,
    "SPACE": 0x20,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "BACKSPACE": 0x08,
}


def key_code(name: str) -> int:
    normalized = name.strip().upper()
    if normalized in VK_NAMES:
        return VK_NAMES[normalized]
    if len(normalized) == 1 and (normalized.isdigit() or "A" <= normalized <= "Z"):
        return ord(normalized)
    raise ValueError(f"Tecla não suportada no perfil: {name!r}")


def enable_dpi_awareness() -> None:
    if not IS_WINDOWS:
        return
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # Per-monitor v2
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


class SingleInstance:
    ERROR_ALREADY_EXISTS = 183

    def __init__(self, name: str = "Local\\TorchBridge.ControllerBridge") -> None:
        if not IS_WINDOWS:
            raise Win32Unavailable("Instância única requer Windows.")
        ctypes.set_last_error(0)
        self.handle = kernel32.CreateMutexW(None, False, name)
        self.already_running = bool(self.handle and ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS)

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None


def show_information(title: str, message: str) -> None:
    if IS_WINDOWS:
        user32.MessageBoxW(None, message, title, 0x00000040)


class WindowLocator:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    GA_ROOT = 2

    def __init__(self, process_names: Iterable[str], title_fragments: Iterable[str]) -> None:
        if not IS_WINDOWS:
            raise Win32Unavailable("TorchBridge requer Windows 10 ou 11.")
        self.process_names = {name.casefold() for name in process_names}
        self.title_fragments = tuple(part.casefold() for part in title_fragments)
        self._cached: int | None = None

    @staticmethod
    def _process_name(pid: int) -> str:
        handle = kernel32.OpenProcess(WindowLocator.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            capacity = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(capacity.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(capacity)):
                return Path(buffer.value).name
            return ""
        finally:
            kernel32.CloseHandle(handle)

    @staticmethod
    def title(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def _matches(self, hwnd: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = self._process_name(pid.value).casefold()
        if process_name in self.process_names:
            return True
        title = self.title(hwnd).casefold()
        return bool(title) and title in self.title_fragments

    def find(self) -> int | None:
        if self._cached and user32.IsWindow(self._cached) and user32.IsWindowVisible(self._cached):
            return self._cached
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def callback(hwnd: int, _lparam: int) -> bool:
            if self._matches(hwnd):
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(callback, 0)
        self._cached = found[0] if found else None
        return self._cached

    @staticmethod
    def client_rect(hwnd: int) -> Rect:
        if user32.IsIconic(hwnd):
            return Rect()
        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return Rect()
        origin = wintypes.POINT(rect.left, rect.top)
        if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return Rect()
        return Rect(origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top)

    @staticmethod
    def is_foreground(hwnd: int) -> bool:
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return False
        return user32.GetAncestor(foreground, WindowLocator.GA_ROOT) == user32.GetAncestor(
            hwnd, WindowLocator.GA_ROOT
        )


class InputInjector:
    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_VIRTUALDESK = 0x4000
    MOUSEEVENTF_ABSOLUTE = 0x8000
    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79

    def __init__(self) -> None:
        if not IS_WINDOWS:
            raise Win32Unavailable("A injeção de entrada requer Windows.")

    @staticmethod
    def _send(item: "INPUT") -> bool:
        return user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)) == 1

    def key(self, name: str, down: bool) -> bool:
        vk = key_code(name)
        scan = user32.MapVirtualKeyW(vk, 0)
        flags = self.KEYEVENTF_SCANCODE | (0 if down else self.KEYEVENTF_KEYUP)
        item = INPUT(type=self.INPUT_KEYBOARD, ki=KEYBDINPUT(0, scan, flags, 0, 0))
        return self._send(item)

    def tap(self, name: str) -> bool:
        down = self.key(name, True)
        up = self.key(name, False)
        return down and up

    def mouse_button(self, button: str, down: bool) -> bool:
        flags = {
            ("left", True): self.MOUSEEVENTF_LEFTDOWN,
            ("left", False): self.MOUSEEVENTF_LEFTUP,
            ("right", True): self.MOUSEEVENTF_RIGHTDOWN,
            ("right", False): self.MOUSEEVENTF_RIGHTUP,
        }[(button, down)]
        item = INPUT(type=self.INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, flags, 0, 0))
        return self._send(item)

    def move(self, x: int, y: int) -> bool:
        left = user32.GetSystemMetrics(self.SM_XVIRTUALSCREEN)
        top = user32.GetSystemMetrics(self.SM_YVIRTUALSCREEN)
        width = max(2, user32.GetSystemMetrics(self.SM_CXVIRTUALSCREEN))
        height = max(2, user32.GetSystemMetrics(self.SM_CYVIRTUALSCREEN))
        normalized_x = round((x - left) * 65535 / (width - 1))
        normalized_y = round((y - top) * 65535 / (height - 1))
        flags = self.MOUSEEVENTF_MOVE | self.MOUSEEVENTF_ABSOLUTE | self.MOUSEEVENTF_VIRTUALDESK
        item = INPUT(
            type=self.INPUT_MOUSE,
            mi=MOUSEINPUT(normalized_x, normalized_y, 0, flags, 0, 0),
        )
        return self._send(item)

    @staticmethod
    def cursor_position() -> tuple[int, int]:
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y


def begin_high_resolution_timer() -> None:
    if IS_WINDOWS:
        winmm.timeBeginPeriod(1)


def end_high_resolution_timer() -> None:
    if IS_WINDOWS:
        winmm.timeEndPeriod(1)


def make_overlay_clickthrough(hwnd: int) -> None:
    if not IS_WINDOWS:
        return
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    style = get_long(hwnd, GWL_EXSTYLE)
    set_long(
        hwnd,
        GWL_EXSTYLE,
        style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE,
    )
