# Camada de integração com a API Win32 via ctypes (sem dependências nativas):
#   - SendInput: teclado (scancode), mouse (posição absoluta) e cliques;
#   - EnumWindows/GetForegroundWindow: localizar o Torchlight e conferir foco;
#   - mutex nomeado (instância única), DPI awareness e estilos de janela do overlay;
#   - winmm.timeBeginPeriod: resolução de 1 ms para o loop de 120 Hz.
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
from typing import Iterable

from .models import Rect


# Atalho: todo o resto do módulo é condicionado a estar no Windows.
IS_WINDOWS = os.name == "nt"


# Exceção para funcionalidades que exigem Windows (levantada fora dele).
class Win32Unavailable(RuntimeError):
    pass


if IS_WINDOWS:
    # DLLs do sistema: user32 (janelas/entrada), kernel32 (processos/mutex), winmm (timer).
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    winmm = ctypes.WinDLL("winmm", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    ULONG_PTR = wintypes.WPARAM

    # Estrutura de evento de mouse do SendInput (posição, flags, dados extras).
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    # Estrutura de evento de teclado do SendInput (VK, scancode e flags).
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    # Evento de hardware (raro) — presente para completar a união da INPUT.
    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    # União: o campo "type" da INPUT decide qual destes três é usado.
    class INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    # _anonymous_ deixa escrever INPUT(type=..., mi=...) sem tocar na união.
    class INPUT(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [("type", wintypes.DWORD), ("union", INPUTUNION)]

    # Tipagem estrita do ctypes: evita erros silenciosos de 32/64 bits e ponteiros.
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


# Teclas nomeadas suportadas no perfil → Virtual-Key Codes do Windows.
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


# Converte o nome do binding em código virtual; caracteres únicos viram o próprio código.
def key_code(name: str) -> int:
    normalized = name.strip().upper()
    if normalized in VK_NAMES:
        return VK_NAMES[normalized]
    # "1".."9" e letras são aceitos como tecla direta (atalhos 1–8 do jogo).
    if len(normalized) == 1 and (normalized.isdigit() or "A" <= normalized <= "Z"):
        return ord(normalized)
    # Nome desconhecido: quem chamou decide (logar e ignorar o binding).
    raise ValueError(f"Tecla não suportada no perfil: {name!r}")


# Declara DPI Per-Monitor v2 (-4); sem isso, coordenadas de cursor/overlay erram com escala ≠ 100%.
def enable_dpi_awareness() -> None:
    if not IS_WINDOWS:
        return
    try:
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2; fallbacks cobrem sistemas antigos.
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # Per-monitor v2
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


# Mutex nomeado do kernel: impede duas instâncias do TorchBridge rodando juntas.
class SingleInstance:
    # Código 183 = ERROR_ALREADY_EXISTS: o mutex já existia (outra instância viva).
    ERROR_ALREADY_EXISTS = 183

    def __init__(self, name: str = "Local\\TorchBridge.ControllerBridge") -> None:
        if not IS_WINDOWS:
            raise Win32Unavailable("Instância única requer Windows.")
        ctypes.set_last_error(0)
        # Cria/abre o mutex; sem o lock inicial (bOwnership=False).
        self.handle = kernel32.CreateMutexW(None, False, name)
        self.already_running = bool(self.handle and ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS)

    # Libera o handle; sem isso o mutex ficaria preso até o processo morrer.
    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None


# Caixa de mensagem informativa (0x40 = MB_ICONINFORMATION).
def show_information(title: str, message: str) -> None:
    if IS_WINDOWS:
        user32.MessageBoxW(None, message, title, 0x00000040)


# Localiza a janela do Torchlight por processo ou título e responde sobre foco/área.
class WindowLocator:
    # Acesso mínimo para ler o nome do executável (funciona sem privilégios).
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    # GA_ROOT: janela raiz da cadeia — comparação de foco correta com janelas filhas.
    GA_ROOT = 2

    def __init__(self, process_names: Iterable[str], title_fragments: Iterable[str]) -> None:
        if not IS_WINDOWS:
            raise Win32Unavailable("TorchBridge requer Windows 10 ou 11.")
        self.process_names = {name.casefold() for name in process_names}
        self.title_fragments = tuple(part.casefold() for part in title_fragments)
        self._cached: int | None = None

    @staticmethod
    # Nome do executável do PID via QueryFullProcessImageNameW (sem exigir admin).
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
    # Texto da barra de título da janela (GetWindowText).
    def title(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    # Uma janela pertence ao jogo se o processo está na lista OU o título coincide.
    def _matches(self, hwnd: int) -> bool:
        # Janelas invisíveis nunca contam (evita falsos positivos).
        if not user32.IsWindowVisible(hwnd):
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = self._process_name(pid.value).casefold()
        if process_name in self.process_names:
            return True
        title = self.title(hwnd).casefold()
        return bool(title) and title in self.title_fragments

    # Acha a janela: usa o cache enquanto ela existir; senão re-enumera com EnumWindows.
    def find(self) -> int | None:
        # Cache ainda válido: evita a varredura cara a cada tick (120 Hz).
        if self._cached and user32.IsWindow(self._cached) and user32.IsWindowVisible(self._cached):
            return self._cached
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        # Callback do EnumWindows: para na primeira janela que casa (False encerra a enumeração).
        def callback(hwnd: int, _lparam: int) -> bool:
            if self._matches(hwnd):
                found.append(hwnd)
                return False
            return True

        user32.EnumWindows(callback, 0)
        # Guarda a primeira janela encontrada (ou None para tentar de novo no próximo tick).
        self._cached = found[0] if found else None
        return self._cached

    @staticmethod
    # Retângulo da área útil (cliente) da janela, convertido para coordenadas de tela.
    def client_rect(hwnd: int) -> Rect:
        # Minimizada: sem área útil; devolve retângulo vazio.
        if user32.IsIconic(hwnd):
            return Rect()
        rect = wintypes.RECT()
        # Falha na consulta: retângulo vazio (tratado como 'sem jogo').
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return Rect()
        origin = wintypes.POINT(rect.left, rect.top)
        if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return Rect()
        return Rect(origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top)

    @staticmethod
    # O jogo está em primeiro plano? Compara as janelas-raiz (GetAncestor GA_ROOT).
    def is_foreground(hwnd: int) -> bool:
        foreground = user32.GetForegroundWindow()
        # Nenhuma janela em foco: definitivamente atrás.
        if not foreground:
            return False
        return user32.GetAncestor(foreground, WindowLocator.GA_ROOT) == user32.GetAncestor(
            hwnd, WindowLocator.GA_ROOT
        )


# Envio de teclado e mouse pelo SendInput — mesmos eventos de um teclado/mouse físicos.
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
    # Envia um único evento; True = o Windows aceitou.
    def _send(item: "INPUT") -> bool:
        return user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)) == 1

    # Tecla por scancode (KEYEVENTF_SCANCODE): independente do layout do teclado.
    def key(self, name: str, down: bool) -> bool:
        vk = key_code(name)
        scan = user32.MapVirtualKeyW(vk, 0)
        # DOWN sem KEYUP; UP adiciona KEYUP (soltar).
        flags = self.KEYEVENTF_SCANCODE | (0 if down else self.KEYEVENTF_KEYUP)
        item = INPUT(type=self.INPUT_KEYBOARD, ki=KEYBDINPUT(0, scan, flags, 0, 0))
        return self._send(item)

    # Toque: pressiona e solta na sequência.
    def tap(self, name: str) -> bool:
        down = self.key(name, True)
        up = self.key(name, False)
        return down and up

    # Clique de mouse pelo flag correspondente (left/right, down/up).
    def mouse_button(self, button: str, down: bool) -> bool:
        flags = {
            ("left", True): self.MOUSEEVENTF_LEFTDOWN,
            ("left", False): self.MOUSEEVENTF_LEFTUP,
            ("right", True): self.MOUSEEVENTF_RIGHTDOWN,
            ("right", False): self.MOUSEEVENTF_RIGHTUP,
        }[(button, down)]
        item = INPUT(type=self.INPUT_MOUSE, mi=MOUSEINPUT(0, 0, 0, flags, 0, 0))
        return self._send(item)

    # Movimento absoluto: coordenadas de tela normalizadas para o espaço 0..65535 do SendInput.
    def move(self, x: int, y: int) -> bool:
        left = user32.GetSystemMetrics(self.SM_XVIRTUALSCREEN)
        top = user32.GetSystemMetrics(self.SM_YVIRTUALSCREEN)
        width = max(2, user32.GetSystemMetrics(self.SM_CXVIRTUALSCREEN))
        height = max(2, user32.GetSystemMetrics(self.SM_CYVIRTUALSCREEN))
        # Normalização proporcional sobre a área virtual (multi-monitor).
        normalized_x = round((x - left) * 65535 / (width - 1))
        normalized_y = round((y - top) * 65535 / (height - 1))
        # ABSOLUTE = coordenadas 0..65535; VIRTUALDESK cobre monitores com X/Y negativos.
        flags = self.MOUSEEVENTF_MOVE | self.MOUSEEVENTF_ABSOLUTE | self.MOUSEEVENTF_VIRTUALDESK
        item = INPUT(
            type=self.INPUT_MOUSE,
            mi=MOUSEINPUT(normalized_x, normalized_y, 0, flags, 0, 0),
        )
        return self._send(item)

    @staticmethod
    # Posição atual do cursor (GetCursorPos).
    def cursor_position() -> tuple[int, int]:
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y


# timeBeginPeriod(1): resolução do timer em 1 ms enquanto o loop roda...
def begin_high_resolution_timer() -> None:
    if IS_WINDOWS:
        winmm.timeBeginPeriod(1)


# ...e restaura a resolução padrão ao encerrar.
def end_high_resolution_timer() -> None:
    if IS_WINDOWS:
        winmm.timeEndPeriod(1)


# Torna a janela do overlay invisível para o mouse: adiciona os estilos WS_EX_.
# Sem isso, o overlay bloquearia cliques do jogo.
def make_overlay_clickthrough(hwnd: int) -> None:
    if not IS_WINDOWS:
        return
    # GWL_EXSTYLE: índice dos estilos estendidos; WS_EX_*: transparente, sem tarefa, layer, sem ativação.
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


if IS_WINDOWS:
    # ctypes.wintypes desta build não traz BITMAPINFOHEADER/BITMAPINFO: define aqui
    # (layout padrão do GDI, 40 bytes de cabeçalho + paleta opcional).
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [
            ("bmiHeader", BITMAPINFOHEADER),
            ("bmiColors", wintypes.DWORD * 3),
        ]

    # Assinaturas do GDI usadas pela captura de tela (tipagem estrita contra erros de 32/64 bits).
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.CreateDIBSection.argtypes = (
        wintypes.HDC,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    )
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.StretchBlt.argtypes = (
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    )
    gdi32.StretchBlt.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteObject.restype = wintypes.BOOL
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.ReleaseDC.restype = ctypes.c_int


# Captura a área útil da janela em uma grade cols×rows de luminância (0..255).
# Duas etapas, ambas à prova de fogo:
#   1. Copia a REGIÃO do jogo do desktop composto em 1:1 para um DIB. A janela
#      do Torchlight renderiza via D3D9 e o DC dela é preto para o GDI (BitBlt/
#      PrintWindow devolvem vazio); a imagem real está na composição da tela,
#      então lemos a tela recortada no retângulo do cliente (ClientToScreen).
#   2. A redução para a grade é feita em Python, amostrando um bloco 3×3 no
#      centro de cada célula. O StretchBlt de downscale do GDI degrada o
#      conteúdo conforme a razão (medido ao vivo: 2:1 perde ~20% do brilho,
#      16:1 devolve quase preto) — não o usamos para reduzir.
# Devolve None em qualquer falha (janela inválida, jogo em exclusivo, etc.).
def capture_client_grid(
    hwnd: int,
    cols: int = 48,
    rows: int = 27,
) -> tuple[list[list[int]], list[list[tuple[int, int, int]]]] | None:
    # Devolve (grade de luminância, grade de cor (b, g, r)) da mesma captura.
    if not IS_WINDOWS or not (user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd)):
        return None
    # Área útil em pixels; janela minimizada/inválida não tem o que capturar.
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)) or rect.right <= 0 or rect.bottom <= 0:
        return None
    # Origem da área útil em coordenadas de tela (início do recorte no desktop).
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    source = user32.GetDC(None)
    if not source:
        return None
    memory = None
    bitmap = None
    try:
        memory = gdi32.CreateCompatibleDC(source)
        if not memory:
            return None
        # DIB de 32 bpp top-down (biHeight negativo) em 1:1: o buffer vem pronto
        # para ler, sem GetDIBits — Largura e altura reais da janela, não da grade.
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = rect.right
        info.bmiHeader.biHeight = -rect.bottom  # negativo = linhas de cima para baixo
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB (sem compressão)
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            memory, ctypes.byref(info), 0, ctypes.byref(bits), None, 0
        )
        if not bitmap or not bits.value:
            return None
        old = gdi32.SelectObject(memory, bitmap)
        # Único StretchBlt da cadeia: cópia 1:1 do desktop (SRCCOPY), sem redução.
        if not gdi32.StretchBlt(
            memory,
            0,
            0,
            rect.right,
            rect.bottom,
            source,
            origin.x,
            origin.y,
            rect.right,
            rect.bottom,
            0x00CC0020,
        ):
            return None
        gdi32.SelectObject(memory, old)
        # Grade por amostragem: bloco 3×3 no centro de cada célula. Devolve as
        # duas visões da mesma captura: luminância (BT.601, para movimento) e cor
        # média (b, g, r) por célula (para features de HUD/painel). O buffer 1:1
        # completo já foi lido uma única vez; a amostragem custa ~cols×rows×9 px.
        stride = rect.right * 4
        buffer = ctypes.string_at(bits.value, stride * rect.bottom)
        luma_grid: list[list[int]] = []
        color_grid: list[list[tuple[int, int, int]]] = []
        for row in range(rows):
            center_y = int((row + 0.5) * rect.bottom / rows)
            luma_line = []
            color_line = []
            for col in range(cols):
                center_x = int((col + 0.5) * rect.right / cols)
                luma_total = 0
                b_total = g_total = r_total = 0
                for dy in (-1, 0, 1):
                    y = min(max(center_y + dy, 0), rect.bottom - 1)
                    row_base = y * stride
                    for dx in (-1, 0, 1):
                        x = min(max(center_x + dx, 0), rect.right - 1)
                        index = row_base + x * 4
                        b = buffer[index]
                        g = buffer[index + 1]
                        r = buffer[index + 2]
                        luma_total += (r * 299 + g * 587 + b * 114) // 1000
                        b_total += b
                        g_total += g
                        r_total += r
                luma_line.append(luma_total // 9)
                color_line.append((b_total // 9, g_total // 9, r_total // 9))
            luma_grid.append(luma_line)
            color_grid.append(color_line)
        return luma_grid, color_grid
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory:
            gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, source)
