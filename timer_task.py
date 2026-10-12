#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Таймер действий — небольшая утилита с окном (Tkinter).

Задаёте время (секунды или минуты), выбираете действие и жмёте «Старт».
По истечении таймера выполняется выбранное действие.

Действия:
  - Предпросмотр (только показать результат, ничего не трогает)  <- безопасно для теста
  - Завершение работы системы (shutdown / poweroff)
  - Выход из системы (logout / завершение сеанса)
  - Спящий режим
  - Перезагрузка
  - Пользовательская команда

Режим --dry-run печатает команду, которую собирается выполнить,
но НЕ выполняет её — удобно тестировать в песочнице.
"""

import sys
import time
import shutil
import platform
import subprocess
from datetime import datetime, timedelta

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
    HAS_TK = True
except Exception:
    HAS_TK = False


# ---------------------------------------------------------------------------
# Защита вывода для EXE-сборки (--windowed): в pythonw-режиме sys.stdout/
# sys.stderr равны None, и любой print() уронил бы приложение. Подменяем их
# безопасными заглушками, если реальный поток недоступен.
# ---------------------------------------------------------------------------
def _ensure_streams():
    class _Null:
        def write(self, *a, **k): return 0
        def flush(self): pass
        def fileno(self): raise OSError("no stream")
        @property
        def encoding(self): return "utf-8"
        isatty = lambda self: False
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, _Null())
            except Exception:
                pass


_ensure_streams()


# ---------------------------------------------------------------------------
# Кодировка консоли: на Windows stdout по умолчанию cp1251/cp1252 и любой
# print с кириллицей/эмодзи падает с UnicodeEncodeError. Переконфигурируем
# потоки в UTF-8 с заменой недопустимых символов (Python 3.7+).
# ---------------------------------------------------------------------------
def _fix_console_encoding():
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_fix_console_encoding()


# ---------------------------------------------------------------------------
# Определение платформы и подходящих команд
# ---------------------------------------------------------------------------

def detect_commands():
    """Возвращает словарь: название действия -> shell-команда для текущей ОС."""
    system = platform.system()  # 'Linux', 'Windows', 'Darwin'
    cmds = {}

    def has(cmd):
        return shutil.which(cmd) is not None

    if system == "Linux":
        use_systemd = has("systemctl")
        if use_systemd:
            cmds["Выключение ПК"] = "systemctl poweroff"
            cmds["Выход из системы"] = "loginctl terminate-user $USER"
            cmds["Спящий режим"] = "systemctl suspend"
            cmds["Перезагрузка"] = "systemctl reboot"
        else:
            cmds["Выключение ПК"] = "shutdown -h now"
            cmds["Выход из системы"] = "pkill -u $USER -TERM"
            cmds["Спящий режим"] = "rtcwake -m mem -g 0 || echo 'suspend недоступен'"
            cmds["Перезагрузка"] = "reboot"
    elif system == "Darwin":
        cmds["Выключение ПК"] = "osascript -e 'tell app \"System Events\" to shut down'"
        cmds["Выход из системы"] = "osascript -e 'tell app \"System Events\" to log out'"
        cmds["Спящий режим"] = "pmset sleepnow"
        cmds["Перезагрузка"] = "osascript -e 'tell app \"System Events\" to restart'"
    elif system == "Windows":
        cmds["Выключение ПК"] = "shutdown /s /t 0"
        cmds["Выход из системы"] = "shutdown /l"
        cmds["Спящий режим"] = "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"
        cmds["Перезагрузка"] = "shutdown /r /t 0"
    else:
        cmds["Выключение ПК"] = "echo 'Неизвестная платформа: выключение недоступно'"
        cmds["Выход из системы"] = "echo 'Неизвестная платформа: выход недоступен'"
        cmds["Спящий режим"] = "echo 'Неизвестная платформа: сон недоступен'"
        cmds["Перезагрузка"] = "echo 'Неизвестная платформа: перезагрузка недоступна'"

    # Специальное безопасное действие для теста
    cmds["Предпросмотр (безопасно)"] = "echo '[ПРЕДПРОСМОТР] действие выполнено бы здесь'"
    return cmds


ACTION_ORDER = [
    "Предпросмотр (безопасно)",
    "Выключение ПК",
    "Выход из системы",
    "Спящий режим",
    "Перезагрузка",
    "Пользовательская команда",
]

# ASCII-алиасы действий: удобны для CLI/CI на Windows, где аргументы с
# кириллицей могут искажаться из-за кодовой страницы консоли (cp1251/cp866).
ACTION_ALIASES = {
    "preview": "Предпросмотр (безопасно)",
    "shutdown": "Выключение ПК",
    "logout": "Выход из системы",
    "sleep": "Спящий режим",
    "restart": "Перезагрузка",
    "custom": "Пользовательская команда",
}


def resolve_action(name):
    """Возвращает каноническое имя действия по названию или алиасу."""
    key = name.strip().lower()
    if key in ACTION_ALIASES:
        return ACTION_ALIASES[key]
    for full in ACTION_ORDER:
        if name.strip() == full:
            return full
    raise ValueError(f"неизвестное действие: {name}")


# ---------------------------------------------------------------------------
# Ядро: планирование и выполнение
# ---------------------------------------------------------------------------

class ActionRunner:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.commands = detect_commands()
        self._proc = None

    def command_for(self, action, custom=""):
        if action == "Пользовательская команда":
            return custom.strip()
        return self.commands.get(action, "")

    def run(self, action, custom=""):
        cmd = self.command_for(action, custom)
        if not cmd:
            raise ValueError(f"Нет команды для действия: {action}")
        if self.dry_run:
            print(f"[DRY-RUN] будет выполнена команда: {cmd}")
            return cmd
        print(f"[EXEC] выполняю: {cmd}")
        if platform.system() == "Windows":
            # Windows: запуск без лишнего консольного окна (важно для EXE-сборки,
            # где stdout/stderr могут отсутствовать).
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._proc = subprocess.Popen(
                cmd, shell=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            return cmd
        self._proc = subprocess.Popen(
            cmd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        out, _ = self._proc.communicate()
        if out:
            print(out.strip())
        return cmd


# ---------------------------------------------------------------------------
# Консольный интерфейс (для теста в песочнице без GUI)
# ---------------------------------------------------------------------------

def parse_duration(text):
    """Принимает '90', '5m', '1h30m', '01:30:00', '90s' -> секунды (int)."""
    text = text.strip().lower().replace(" ", "")
    if not text:
        raise ValueError("пустое значение")
    # HH:MM:SS или MM:SS
    if ":" in text:
        parts = [int(p) for p in text.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        h, m, s = parts[-3], parts[-2], parts[-1]
        return h * 3600 + m * 60 + s
    # суффиксы
    total = 0
    num = ""
    matched = False
    for ch in text:
        if ch.isdigit():
            num += ch
        elif ch in ("s", "m", "h", "д", "м", "ч"):
            if not num:
                raise ValueError(f"не понял: {text}")
            n = int(num)
            unit = {"s": 1, "m": 60, "h": 3600, "д": 86400, "м": 60, "ч": 3600}[ch]
            total += n * unit
            num = ""
            matched = True
        else:
            raise ValueError(f"неизвестный символ '{ch}'")
    if num and not matched:
        # просто число -> секунды
        return int(num)
    if not matched:
        raise ValueError(f"не понял: {text}")
    return total


def cli_mode(argv):
    """python timer_task.py --cli --time 10 --action 'Предпросмотр (безопасно)' [--dry-run]"""
    opts = {"time": "10", "action": "Предпросмотр (безопасно)", "custom": "", "list": False}
    dry = "--dry-run" in argv
    it = iter(range(len(argv)))
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--time" and i + 1 < len(argv):
            opts["time"] = argv[i + 1]; i += 2; continue
        if a == "--action" and i + 1 < len(argv):
            opts["action"] = argv[i + 1]; i += 2; continue
        if a == "--custom" and i + 1 < len(argv):
            opts["custom"] = argv[i + 1]; i += 2; continue
        i += 1

    runner = ActionRunner(dry_run=dry)

    if opts["list"]:
        for name in ACTION_ORDER:
            print(f"  - {name}: {runner.command_for(name)}")
        return 0

    try:
        action = resolve_action(opts["action"])
    except ValueError as e:
        print(f"✖ {e}")
        return 2
    secs = parse_duration(opts["time"])
    target = datetime.now() + timedelta(seconds=secs)
    print(f"⏳ Таймер: {secs} сек. Действие: «{action}»")
    print(f"   Цель: {target.strftime('%H:%M:%S')}")
    end = time.time() + secs
    while time.time() < end:
        left = int(end - time.time())
        print(f"\r   осталось {left:>4} сек ...", end="", flush=True)
        time.sleep(0.5)
    print("\r   время вышло!          ")
    cmd = runner.run(action, opts["custom"])
    print(f"✔ Готово. Команда: {cmd}")
    return 0


# ---------------------------------------------------------------------------
# Графический интерфейс (Tkinter) — современный минималистичный дизайн (MVP)
#   1) Тёмная тема, скруглённые карточки/кнопки, акцентные цвета
#   2) Окно настройки -> после «СТАРТ» плавно исчезает (fade-out)
#   3) Таймер: БОЛЬШИЕ КРАСНЫЕ ЦИФРЫ на ПРОЗРАЧНОМ фоне (без чёрного экрана)
#   4) По истечении времени действие выполняется СРАЗУ, без подтверждения.
#      Отмена таймера не предусмотрена (по требованию пользователя).
# Функционал (парсер времени, пресеты, действия, CLI) — без изменений.
# ---------------------------------------------------------------------------

import os as _os

# ---- палитра (modern dark theme) ------------------------------------------
ACCENT = "#e5484d"           # фирменный красный (акценты, СТАРТ, таймер)
ACCENT_HOVER = "#f0565b"     # подсветка при наведении
ACCENT_DOWN = "#c23a3f"      # нажатие
FG_RED = "#ff2d34"           # цифры таймера
BG_DARK = "#0d0d0d"          # фон на ОС без color-key (X11): полупрозрачный тёмный

# Цвет-ключ прозрачности. На Windows используется magic-color + -transparentcolor
# (реальная перфорация окна): цифры остаются полностью непрозрачными и яркими,
# а фон становится прозрачным — сквозь него видно рабочий стол.
# На X11/мезомаге такой механизм недоступен, поэтому там применяется полу-
# прозрачность всего окна (-alpha), имитирующая прозрачный фон.
TRANSPARENT_KEY = "#fe00fe"


def _supports_transparentcolor(win):
    """True, если ОС поддерживает сквозную прозрачность по цвету-ключу."""
    if sys.platform != "win32":
        return False
    try:
        win.attributes("-transparentcolor", TRANSPARENT_KEY)
        return True
    except tk.TclError:
        return False


def _make_click_through(win):
    """Windows: окно не перехватывает клики мыши (клик сквозь цифры)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
        user32 = ctypes.windll.user32
        get, set_ = (user32.GetWindowLongW, user32.SetWindowLongW) \
            if ctypes.sizeof(ctypes.c_void_p) == 4 else \
            (user32.GetWindowLongPtrW, user32.SetWindowLongPtrW)
        style = get(hwnd, GWL_EXSTYLE)
        set_(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT)
    except Exception:
        pass                              # не критично: просто клики останутся за окном


def _overlay_bg_color(win):
    """Фон оверлея: цвет-ключ (Win, реальная прозрачность) или полупрозрачный тёмный."""
    if _supports_transparentcolor(win):
        return TRANSPARENT_KEY
    return BG_DARK


def _fmt_hms(left):
    h, rem = divmod(int(left), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _fit_font(label, text, base_size):
    """Подбирает размер шрифта так, чтобы текст помещался в экран."""
    size = base_size
    while size > 24:
        label.config(font=("DejaVu Sans Mono", size, "bold"))
        label.update_idletasks()
        w = label.winfo_reqwidth()
        if w < label.winfo_screenwidth() * 0.9:
            break
        size -= 8
    return size


def _set_alpha(win, value):
    """Атрибут -alpha поддерживается не всеми окнами/менеджерами окон."""
    try:
        win.attributes("-alpha", value)
        return True
    except tk.TclError:
        return False


def fade_window(win, on_done, step_ms=25, delta=0.06):
    """Плавное затухание окна (alpha 1.0 -> 0.0), затем on_done().

    Если прозрачность не поддерживается — просто скрываем окно сразу.
    """
    if not _set_alpha(win, 1.0):
        try:
            win.withdraw()
        except tk.TclError:
            pass
        on_done()
        return

    alpha = [1.0]

    def do_fade():
        alpha[0] -= delta
        if alpha[0] <= 0:
            try:
                win.withdraw()
            except tk.TclError:
                pass
            on_done()
            return
        if not _set_alpha(win, max(0.0, alpha[0])):
            try:
                win.withdraw()
            except tk.TclError:
                pass
            on_done()
            return
        win.after(step_ms, do_fade)

    do_fade()


# ---------------------------------------------------------------------------
# Быстрые пресеты времени (по эскизу пользователя)
# ---------------------------------------------------------------------------
TIME_PRESETS = [(5,), (15,), (30,), (60,), (120,), (300,),
                (600,), (900,), (1800,), (3600,), (7200,)]


def _fmt_secs(secs):
    """Короткая подпись пресета: 5 -> '5с', 60 -> '1м', 7200 -> '2ч'."""
    secs = int(secs)
    if secs % 3600 == 0:
        return f"{secs // 3600}ч"
    if secs % 60 == 0:
        return f"{secs // 60}м"
    return f"{secs}с"


# ---------------------------------------------------------------------------
# Современный дизайн: тёмная тема через ttk-themes + скруглённые Canvas-кнопки
# ---------------------------------------------------------------------------

CARD_BG = "#1b1f27"          # фон карточек/поля ввода
CANVAS_BG = "#141820"        # фон окна
TEXT_MAIN = "#e8eaed"        # основной текст
TEXT_MUTED = "#8a93a2"       # второстепенный текст
BORDER_COL = "#2a303c"       # тонкие разделители

UI_FONT = ("Segoe UI", 10)
UI_FONT_BOLD = ("Segoe UI", 10, "bold")


def _apply_dark_theme(root):
    """Тёмная тема ttk-виджетов. Пакет ttkthemes ставится опционально
    (в EXE включён); если его нет — базовый clam-стиль с тёмной палитрой."""
    style = ttk.Style(root)
    try:
        import ttkthemes
        style.theme_use("black")
        return
    except Exception:
        pass
    try:
        style.theme_use("clam")
    except tk.TclError:
        return
    base = {
        "background": CANVAS_BG, "foreground": TEXT_MAIN,
        "fieldbackground": CARD_BG, "bordercolor": BORDER_COL,
        "lightcolor": CANVAS_BG, "darkcolor": CANVAS_BG,
        "lightbackground": CANVAS_BG, "darkbackground": CANVAS_BG,
        "troughcolor": CARD_BG, "focuscolor": ACCENT,
        "selectbackground": ACCENT, "selectforeground": "white",
        "font": UI_FONT,
    }
    for el in (".", "TFrame", "TLabel", "TLabelframe", "TLabelframe.Label",
               "TCheckbutton", "TRadiobutton", "TNotebook.Tab"):
        try:
            style.configure(el, **base)
        except tk.TclError:
            pass
    for el in ("TEntry", "TCombobox"):
        try:
            style.configure(el, **{**base, "arrowcolor": TEXT_MUTED})
            style.map(el, fieldbackground=[("readonly", CARD_BG)],
                      foreground=[("readonly", TEXT_MAIN)])
        except tk.TclError:
            pass
    for el in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        try:
            style.configure(el, background=CARD_BG, arrowcolor=TEXT_MUTED,
                            bordercolor=CANVAS_BG, troughcolor=CANVAS_BG)
        except tk.TclError:
            pass


class RoundButton(tk.Canvas):
    """Минималистичная скруглённая кнопка (Canvas) с hover/press-эффектами.
    Интерфейс совместим с tk.Button: config(text=/command=), .invoke()."""

    def __init__(self, master, text="", command=None, bg=ACCENT,
                 hover=ACCENT_HOVER, down=ACCENT_DOWN, fg="white",
                 font=("Segoe UI", 12, "bold"), radius=10, height=40,
                 outline="", **kw):
        super().__init__(master, height=height, highlightthickness=0,
                         bg=master.cget("bg"), bd=0, **kw)
        self._command = command
        self._text = text
        self._bg, self._hover, self._down = bg, hover, down
        self._fg, self._font, self._radius = fg, font, radius
        self._outline = outline
        self._state = "normal"
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set(hover) if self._state == "normal" else None)
        self.bind("<Leave>", lambda e: self._set(self._bg))
        self.bind("<ButtonPress-1>", lambda e: self._set(down))
        self.bind("<ButtonRelease-1>", lambda e: self._release())

    def _set(self, color):
        self._cur = color
        self._draw()

    def _release(self):
        inside = (0 <= self.winfo_pointerx() - self.winfo_rootx() <= self.winfo_width()
                  and 0 <= self.winfo_pointery() - self.winfo_rooty() <= self.winfo_height())
        self._set(self._hover if inside else self._bg)
        if inside and self._command:
            self._command()

    def invoke(self):
        if self._command:
            self._command()

    def config(self, cnf=None, **kw):
        cmd = kw.pop("command", "__unset__")
        if cmd != "__unset__":
            self._command = cmd
        for key in ("text", "bg", "fg", "font", "radius", "height"):
            if key in kw:
                setattr(self, "_" + key, kw.pop(key))
        if cnf:
            for key, val in dict(cnf).items():
                if key == "command":
                    self._command = val
                elif key in ("text", "bg", "fg", "font", "radius", "height"):
                    setattr(self, "_" + key, val)
        self._draw()
        try:
            super().config(**kw)
        except tk.TclError:
            pass

    configure = config

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2, x2-r,y2,
               x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return
        r = min(self._radius, h // 2)
        self._round_rect(1, 1, w-1, h-1, r, fill=getattr(self, "_cur", self._bg),
                         outline=self._outline or getattr(self, "_cur", self._bg), width=1)
        self.create_text(w // 2, h // 2, text=self._text, fill=self._fg,
                         font=self._font)


def gui_mode():
    if not HAS_TK:
        print("Tkinter недоступен в этой среде. Используйте --cli.")
        print("Установите: sudo apt-get install python3-tk")
        return 1

    # --dry-run/--sandbox поддерживается и в GUI: полезно для тестов.
    runner = ActionRunner(dry_run=("--dry-run" in sys.argv or "--sandbox" in sys.argv))

    # ---------------- окно настройки (современный минимализм) ----------------
    root = tk.Tk()
    import os as _os
    root.title(_os.environ.get("TK_TITLE", "⏰ Таймер выключения"))
    root.geometry("560x460")
    root.resizable(False, False)
    root.configure(bg=CANVAS_BG)
    _apply_dark_theme(root)

    status_var = tk.StringVar(value="Готово")

    # --- заголовок ---
    head = tk.Frame(root, bg=CANVAS_BG)
    head.pack(fill="x", padx=20, pady=(16, 4))
    tk.Label(head, text="Таймер выключения", bg=CANVAS_BG, fg=TEXT_MAIN,
             font=("Segoe UI", 15, "bold")).pack(side="left")
    tk.Label(head, text="MVP", bg=ACCENT, fg="white",
             font=("Segoe UI", 8, "bold"), padx=7, pady=1).pack(side="left", padx=(8, 0))

    # --- карточка времени: большое поле + −/+ под ним ---
    card_t = tk.Frame(root, bg=CARD_BG, highlightbackground=BORDER_COL,
                      highlightthickness=1)
    card_t.pack(fill="x", padx=20, pady=(8, 6))

    time_var = tk.StringVar(value="00:05:00")

    def _bump(delta):
        try:
            cur = parse_duration(time_var.get())
        except Exception:
            cur = 0
        time_var.set(_fmt_hms(max(0, cur + delta)))

    ent_row = tk.Frame(card_t, bg=CARD_BG)
    ent_row.pack(pady=(12, 2))
    ent_time = tk.Entry(ent_row, textvariable=time_var, width=11, justify="center",
                        bg=CARD_BG, insertbackground=TEXT_MAIN, relief="flat",
                        font=("Consolas", 26, "bold"), fg=TEXT_MAIN)
    ent_time.pack()
    # нижняя линия-подчёркивание поля (минимализм)
    tk.Frame(card_t, bg=ACCENT, height=2, width=180).pack(pady=(2, 0))

    adj = tk.Frame(card_t, bg=CARD_BG)
    adj.pack(pady=(8, 12))
    RoundButton(adj, text="− 1 мин", width=96, height=30, bg=CARD_BG,
                hover="#242a35", down="#2b3240", fg=TEXT_MAIN,
                font=UI_FONT, radius=8, outline=BORDER_COL,
                command=lambda: _bump(-60)).pack(side="left", padx=4)
    tk.Label(adj, text="часы : минуты : секунды", bg=CARD_BG, fg=TEXT_MUTED,
             font=("Segoe UI", 9)).pack(side="left", padx=10)
    RoundButton(adj, text="+ 1 мин", width=96, height=30, bg=CARD_BG,
                hover="#242a35", down="#2b3240", fg=TEXT_MAIN,
                font=UI_FONT, radius=8, outline=BORDER_COL,
                command=lambda: _bump(60)).pack(side="left", padx=4)

    # --- сетка кнопок быстрых пресетов ---
    grid = tk.Frame(root, bg=CANVAS_BG)
    grid.pack(fill="x", padx=20, pady=(4, 2))
    cols = 4
    preset_btns = []
    for i, (secs,) in enumerate(TIME_PRESETS):
        r, c = divmod(i, cols)
        b = RoundButton(grid, text=_fmt_secs(secs), height=30, bg=CARD_BG,
                        hover="#242a35", down="#2b3240", fg=TEXT_MAIN,
                        font=UI_FONT, radius=8, outline=BORDER_COL,
                        command=lambda s=secs: time_var.set(
                            f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"))
        b.grid(row=r, column=c, padx=3, pady=3, sticky="we")
        preset_btns.append(b)
    # последняя ячейка — «Сбросить»
    RoundButton(grid, text="✖ сброс", height=30, bg=CARD_BG,
                hover="#242a35", down="#2b3240", fg=TEXT_MUTED,
                font=UI_FONT, radius=8, outline=BORDER_COL,
                command=lambda: time_var.set("00:00:00")
                ).grid(row=len(TIME_PRESETS) // cols,
                       column=len(TIME_PRESETS) % cols, padx=3, pady=3, sticky="we")
    for c in range(cols):
        grid.columnconfigure(c, weight=1)

    # --- выбор действия (компактная строка) ---
    act = tk.Frame(root, bg=CANVAS_BG)
    act.pack(fill="x", padx=20, pady=(6, 2))
    tk.Label(act, text="Действие:", bg=CANVAS_BG, fg=TEXT_MUTED,
             font=UI_FONT).pack(side="left")
    cb_action = ttk.Combobox(act, values=ACTION_ORDER, state="readonly", width=24)
    cb_action.pack(side="left", padx=8)
    cb_action.set(ACTION_ORDER[1])          # по умолчанию — Выключение ПК
    ent_custom = ttk.Entry(act, width=16)   # для «Пользовательской команды»
    ent_custom.pack(side="left", padx=(4, 0))

    # --- большая акцентная кнопка СТАРТ ---
    mid = tk.Frame(root, bg=CANVAS_BG)
    mid.pack(fill="x", padx=20, pady=(10, 4))
    btn_start = RoundButton(mid, text="▶  С Т А Р Т", height=46, bg=ACCENT,
                            hover=ACCENT_HOVER, down=ACCENT_DOWN, fg="white",
                            font=("Segoe UI", 14, "bold"), radius=12)
    btn_start.pack(fill="x")

    tk.Label(root, textvariable=status_var, bg=CANVAS_BG, fg=ACCENT_HOVER,
             font=UI_FONT).pack(fill="x", padx=20)
    tk.Label(
        root,
        text="После «СТАРТ» это окно плавно исчезнет, а на экране появится\n"
             "большой таймер с красными цифрами на прозрачном фоне.\n"
             "Отмена не предусмотрена — по истечении времени действие\n"
             "выполняется автоматически.",
        fg=TEXT_MUTED, bg=CANVAS_BG, justify="left", font=("Segoe UI", 9),
    ).pack(fill="x", padx=20, pady=(0, 12))

    # ---------------- полноэкранный таймер ----------------
    # По требованию пользователя: БЕЗ чёрного экрана — только большие
    # красные цифры на ПРОЗРАЧНОМ фоне (сквозь них видно рабочий стол).
    #
    # Реализация прозрачности:
    #  * Windows: magic-color (#fe00fe) + атрибут -transparentcolor —
    #    реальная «перфорация» окна: фон полностью прозрачен, цифры
    #    остаются яркими и непрозрачными. Дополнительно окно делается
    #    click-through (клики проходят сквозь цифры на рабочий стол).
    #  * X11/macOS (для разработки): сквозная прозрачность по цвету
    #    недоступна, поэтому имитация — полупрозрачное тёмное окно
    #    (-alpha), чтобы песочница была репрезентативной.
    #
    # ВАЖНО (фикс бага на Windows): нельзя комбинировать
    # overrideredirect(True) с -fullscreen/-alpha (TclError), поэтому
    # используется обычное окно с -fullscreen + -toolwindow.
    overlay = tk.Toplevel(root)
    overlay.title("Таймер")
    ov_bg = _overlay_bg_color(overlay)      # color-key (Win) или BG_DARK (X11)
    overlay.configure(bg=ov_bg)
    try:
        overlay.attributes("-fullscreen", True)
    except tk.TclError:
        pass                                # нет менеджера окон — не критично
    if sys.platform == "win32":
        try:
            overlay.attributes("-toolwindow", True)  # без кнопки в панели задач
        except tk.TclError:
            pass
        _make_click_through(overlay)        # клики сквозь прозрачный фон
    overlay.withdraw()                      # пока скрыт

    action_lbl = tk.Label(overlay, text="", bg=ov_bg, fg="#c9d1d9",
                          font=("Segoe UI", 16, "bold"))
    action_lbl.place(relx=0.5, rely=0.18, anchor="center")

    # подсказка НЕ нужна: отмены таймера нет (по требованию)

    time_lbl = tk.Label(overlay, text="00:00", bg=ov_bg, fg=FG_RED,
                        font=("DejaVu Sans Mono", 200, "bold"))
    time_lbl.place(relx=0.5, rely=0.5, anchor="center")

    state = {"after_id": None, "end_ts": 0, "running": False,
             "base_font": 200, "last_left": -1}

    def show_overlay():
        """Оверлей появляется сразу (без fade-in на Windows: там -alpha
        затемнил бы и сами цифры; прозрачным остаётся только фон)."""
        overlay.deiconify()
        overlay.lift()
        if sys.platform != "win32":
            # имитация прозрачного фона на X11: полупрозрачное окно
            _set_alpha(overlay, 0.75)
        start_countdown()

    def start_countdown():
        state["running"] = True
        state["last_left"] = -1
        tick()

    def tick():
        if not state["running"]:
            return
        left = max(0, int(round(state["end_ts"] - time.time())))
        if left != state["last_left"]:
            state["last_left"] = left
            txt = _fmt_hms(left)
            time_lbl.config(text=txt)
            _fit_font(time_lbl, txt, state["base_font"])
            # последние 10 секунд — мигание ярче/тусклее
            if left <= 10 and left > 0:
                time_lbl.config(fg="#ff1f1f" if left % 2 == 0 else "#7a0f0f")
        if left <= 0:
            fire()
            return
        state["after_id"] = overlay.after(200, tick)

    def back_to_settings(msg=None):
        """Возврат из оверлея в окно настройки."""
        state["running"] = False
        if state["after_id"]:
            try:
                overlay.after_cancel(state["after_id"])
            except tk.TclError:
                pass
            state["after_id"] = None
        overlay.withdraw()
        root.deiconify()
        _set_alpha(root, 1.0)
        root.lift()
        root.focus_force()
        if msg:
            status_var.set(msg)

    # Отмена таймера НЕ предусмотрена (по требованию пользователя):
    # ни Esc, ни клик по оверлею не останавливают отсчёт.

    def fire():
        """Время вышло — действие выполняется СРАЗУ, без подтверждения."""
        state["running"] = False
        action = cb_action.get()
        custom = ent_custom.get()
        time_lbl.config(text="00:00", fg=FG_RED)
        cmd = runner.command_for(action, custom)

        if action == "Предпросмотр (безопасно)":
            # сразу показываем результат и возвращаемся в окно настройки
            overlay.after(1500, lambda: back_to_settings(f"✔ Предпросмотр выполнен: {cmd}"))
            return

        # опасные действия выполняются немедленно, БЕЗ окна подтверждения
        try:
            runner.run(action, custom)
            back_to_settings(f"✔ Выполнено: {action}")
        except Exception as e:
            back_to_settings(f"✖ Ошибка: {e}")

    def start():
        raw = time_var.get().strip()
        try:
            secs = parse_duration(raw)
        except Exception as e:
            messagebox.showerror("Ошибка времени", str(e), parent=root)
            return
        if secs <= 0:
            messagebox.showerror("Ошибка", "Время должно быть > 0", parent=root)
            return
        action = cb_action.get()

        # по эскизу/ТЗ: окно настройки плавно исчезает, затем появляется
        # полноэкранный таймер с большими красными цифрами (fade-in)
        time_lbl.config(text=_fmt_hms(secs), fg=FG_RED)
        action_lbl.config(text=f"⏳ Действие: {action}")
        state["base_font"] = _fit_font(time_lbl, _fmt_hms(secs), 200)
        state["end_ts"] = time.time() + secs

        def after_fade():
            show_overlay()          # плавное появление + старт отсчёта

        fade_window(root, after_fade)

    # Отмена не предусмотрена: биндов на Esc/клик нет.
    btn_start.config(command=start)

    root.protocol("WM_DELETE_WINDOW", lambda: (overlay.destroy(), root.destroy()))
    root.mainloop()
    return 0


# ---------------------------------------------------------------------------

def main():
    argv = sys.argv[1:]
    if "--cli" in argv or not HAS_TK:
        return cli_mode(argv)
    return gui_mode()


if __name__ == "__main__":
    raise SystemExit(main())
