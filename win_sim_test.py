# -*- coding: utf-8 -*-
"""Регресс-тест нового интерфейса (по эскизу) + защита от бага Windows.

Проверяет:
 1) В окне настройки есть поле времени, кнопки +/-, сетка пресетов,
    выпадающий список действий и большая кнопка СТАРТ.
 2) Клик по пресету ставит время в поле; клик СТАРТ запускает таймер.
 3) Окно настройки плавно гаснет, затем появляется полноэкранный оверлей
    с БОЛЬШИМИ КРАСНЫМИ цифрами обратного отсчёта (fade-in).
 4) Эмуляция строгого Windows-Tk: любой вызов wm_attributes(-fullscreen/-alpha)
    на окне с override-redirect -> TclError. Приложение не должно использовать
    overrideredirect вообще.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tkinter as tk
import timer_task

class FakeMB:
    @staticmethod
    def askyesno(*a, **k): return False
    @staticmethod
    def showerror(*a, **k): print("SHOWERROR:", a)
timer_task.messagebox = FakeMB

_real_tk = tk.Tk
_roots = []
_oredirect = set()

_orig_wm_attributes = tk.Wm.wm_attributes
def strict_wm_attributes(self, winname, *args, **kw):
    # имя окна относительно root: .!toplevel...
    path = str(self._w if not hasattr(winname,'_w') else winname)
    tgt = winname if isinstance(winname, str) else None
    # определяем widget по self (Tk instance) + name
    try:
        w = self.tk.call('winfo', 'id', f'{self._w}' if tgt in (None,'.') else f'{self._w}{tgt}')
    except Exception:
        w = None
    key = (str(self), tgt or '.')
    attr = args[0] if args else (kw.get('-fullscreen') is not None and '-fullscreen')
    if key in _oredirect and attr in ('-fullscreen', '-alpha'):
        raise tk.TclError(f"can't set {attr} attribute for \"{key[1]}\": override-redirect flag is set")
    return _orig_wm_attributes(self, winname, *args, **kw)
tk.Wm.wm_attributes = strict_wm_attributes

_orig_or = tk.Wm.wm_overrideredirect
def strict_or(self, windowName=None, newValue=None):
    if newValue:
        _oredirect.add((str(self), windowName or '.'))
    return _orig_or(self, windowName, newValue)
tk.Wm.wm_overrideredirect = strict_or

class MyTk(_real_tk):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        _roots.append(self)
tk.Tk = MyTk

def walk(w, out):
    for c in w.winfo_children():
        out.append(c); walk(c, out)

RESULTS = []
def check(name, cond):
    RESULTS.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)


def _bt(b):
    """Текст кнопки: наш RoundButton хранит его в атрибуте _text."""
    return b._text if isinstance(b, timer_task.RoundButton) else str(b.cget("text"))


def _plain(root):
    out = []
    walk(root, out)
    return out


def phase1():
    r = _roots[0]
    # RoundButton наследует tk.Canvas, а winfo_class() у Canvas возвращает
    # "Canvas" — добавляем их в выборку явно.
    rb = [w for w in (lambda o: (walk(r, o), o)[1])([]) if isinstance(w, timer_task.RoundButton)]
    allw = rb + _plain(r)
    entries = [w for w in allw if w.winfo_class() in ("Entry", "TEntry")]
    buttons = [w for w in allw if isinstance(w, timer_task.RoundButton)
               or w.winfo_class() in ("Button","TButton")]
    combos  = [w for w in allw if w.winfo_class()=="TCombobox"]
    labels  = [w for w in allw if w.winfo_class() in ("Label","TLabel")]
    check("поле времени есть", len(entries) >= 1)
    check("кнопки +/- есть", any("+ 1 мин" in _bt(b) or "− 1 мин" in _bt(b) or _bt(b) in ("+","−") for b in buttons))
    texts = [_bt(b) for b in buttons]
    check("пресеты времени (5с..2ч)", all(t in texts for t in ["5с","15с","30с","1м","2м","5м","10м","15м","30м","1ч","2ч"]))
    check("выпадающий список действий", len(combos) == 1)
    check("большая кнопка СТАРТ", any("С Т А Р Т" in t.replace("  ", " ") or "СТАРТ" in t.replace(" ", "") for t in texts))
    check("метки часы/минуты/секунды", any("часы" in str(l.cget("text")) and "минуты" in str(l.cget("text")) and "секунды" in str(l.cget("text")) for l in labels))
    # клик по пресету "15с"
    b15 = [b for b in buttons if _bt(b)=="15с"][0]
    b15.invoke()
    check("клик по пресету ставит 00:00:15", entries[0].get()=="00:00:15")
    # выбрать безопасное действие и 5 сек для теста
    combos[0].set("Предпросмотр (безопасно)")
    entries[0].delete(0,"end"); entries[0].insert(0,"00:00:05")
    start_btn = [b for b in buttons if "СТАРТ" in _bt(b).replace(" ", "")][0]
    start_btn.invoke()
    r.after(600, phase2)

def phase2():
    r = _roots[0]
    tops = [w for w in r.winfo_children() if w.winfo_class()=="Toplevel"]
    tl = tops[0]
    alpha_ok = True
    try:
        a = float(tl.attributes("-alpha")[1]) if tl.state()!="withdrawn" else 0.0
    except Exception:
        a = 1.0
    check("оверлей появился после fade-out настроек", tl.state() != "withdrawn" or a > 0.0)
    check("окно настройки скрыто (withdrawn)", r.state()=="withdrawn")
    allw=[]; walk(tl, allw)
    big = max([w for w in allw if w.winfo_class()=="Label" and str(w.cget("text")).strip().replace("\u2009"," ").replace(" ","").count(":")==1 and len(str(w.cget("text")).strip())>=5],
              key=lambda l: len(str(l.cget("font"))))
    txt = str(big.cget("text")).replace("\u2009", " ").strip()  # тонкие пробелы -> обычные
    fg=str(big.cget("fg"))
    font_size = int(str(big.cget("font")).split()[-2]) if str(big.cget("font")).split()[-2].isdigit() else int(str(big.cget("font")).rsplit(None,2)[1])
    check("цифры красные (FG_RED)", fg.lower() in (timer_task.FG_RED.lower(), "#7a0f0f"))
    check("шрифт большой (>=60px)", font_size >= 60)
    check("формат MM:SS", len(txt)==5 and txt[2]==":")
    r.after(2500, phase3)

def phase3():
    r = _roots[0]
    tops = [w for w in r.winfo_children() if w.winfo_class()=="Toplevel"]
    tl = tops[0]
    allw=[]; walk(tl, allw)
    big = max([w for w in allw if w.winfo_class()=="Label" and str(w.cget("text")).strip().replace("\u2009"," ").replace(" ","").count(":")==1 and len(str(w.cget("text")).strip())>=5],
              key=lambda l: len(str(l.cget("font"))))
    txt = str(big.cget("text")).replace("\u2009", " ").strip()
    secs = int(txt[:2])*60+int(txt[3:]) if len(txt)==5 else 999
    check("таймер считает вниз (<5с)", secs < 5)
    r.after(3800, phase4)   # ждём окончания таймера 5с + возврата превью

_p4tries = [0]
def phase4():
    r = _roots[0]
    _p4tries[0] += 1
    if r.state() != "normal" and _p4tries[0] < 10:
        r.after(500, phase4)
        return
    check("после истечения окно вернулось", r.state()=="normal")
    check("overrideredirect НЕ использовался (защита Windows)", len(_oredirect)==0)
    failed = [n for n,ok in RESULTS if not ok]
    print("TOTAL:", len(RESULTS), "FAILED:", len(failed), failed)
    r.quit()
    sys.exit(1 if failed else 0)

MyTk.mainloop = lambda self,*a,**k:(self.after(700, phase1), _real_tk.mainloop(self))
# ТЕСТ: никогда не выполнять реальные системные команды (выключение и т.п.)
sys.argv.append("--dry-run")
rc = timer_task.gui_mode()
print("EXIT RC=", rc, "results:", sum(1 for _,ok in RESULTS if ok), "/", len(RESULTS))
sys.exit(rc)
