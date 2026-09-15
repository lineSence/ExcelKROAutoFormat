"""Интерфейс компаньона: значок в трее и окно настроек.

Другого способа настройки у программы нет: файлов окружения в
проекте не используем. Окно собирается на tkinter — он входит в
обычную сборку Python для Windows.

Значок в трее необязателен: без `pystray` программа работает без трея
(режим `--без-трея`), а подсказки уходят в журнал.
"""

from __future__ import annotations

import logging
import threading
import webbrowser

from .config import Settings

logger = logging.getLogger("companion.ui")

# Подписи полей окна настроек: «имя настройки, подпись, подсказка».
FIELDS = (
    ("server_url", "Адрес сервера", "Через туннель обычно http://127.0.0.1:8000"),
    ("auth_user", "Имя для входа", "Нужно только при внешнем адресе сервера"),
    ("auth_password", "Пароль", "Пустое поле — оставить как было"),
    ("inbox", "Папка со сверками из 1С", "Отсюда файлы уходят на сервер"),
    ("outbox", "Папка для готовых сверок", "Сюда ложатся файл и архив снимков"),
    ("archive", "Папка для сданных исходников", "Не задана — исходник останется на месте"),
    ("errors", "Папка для сбоёв", "Туда уезжают файлы, которые сервер не взял"),
    ("poll_seconds", "Опрос сервера, сек", "Как часто спрашивать очередь выгрузок"),
    ("stable_seconds", "Файл должен отлежаться, сек", "Защита от недокопированных файлов"),
)
FLAGS = (
    ("open_browser", "Открывать страницу сверки самостоятельно"),
    ("strict", "Только чёткие пересорты"),
)


def settings_window(settings: Settings, on_save=None) -> None:
    """Открывает окно настроек. Вызывать только из главного потока."""
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title("Настройки компаньона ExcelKRO")
    root.geometry("640x560")
    values: dict[str, object] = {}

    for row, (name, title, hint) in enumerate(FIELDS):
        tk.Label(root, text=title, anchor="w").grid(row=row * 2, column=0, sticky="w", padx=10, pady=(8, 0))
        holder = tk.StringVar()
        if name == "auth_password":
            holder.set("")
            entry = tk.Entry(root, textvariable=holder, show="*", width=54)
            hint = f"{hint} (сейчас: {settings.mask_password()})"
        else:
            holder.set(str(getattr(settings, name, "")))
            entry = tk.Entry(root, textvariable=holder, width=54)
        entry.grid(row=row * 2, column=1, sticky="we", padx=10, pady=(8, 0))
        values[name] = holder
        if name in ("inbox", "outbox", "archive", "errors"):
            tk.Button(
                root,
                text="Выбрать…",
                command=lambda holder=holder: holder.set(filedialog.askdirectory() or holder.get()),
            ).grid(row=row * 2, column=2, padx=(0, 10), pady=(8, 0))
        tk.Label(root, text=hint, anchor="w", fg="#666").grid(row=row * 2 + 1, column=1, sticky="w", padx=10)

    base_row = len(FIELDS) * 2
    for number, (name, title) in enumerate(FLAGS):
        holder = tk.BooleanVar(value=bool(getattr(settings, name, False)))
        tk.Checkbutton(root, text=title, variable=holder).grid(row=base_row + number, column=1, sticky="w", padx=10)
        values[name] = holder

    def save() -> None:
        fresh = settings.with_form({name: holder.get() for name, holder in values.items()})
        try:
            path = fresh.save()
        except OSError as error:
            messagebox.showerror("Настройки не сохранены", f"{error}")
            return
        troubles = fresh.troubles()
        if troubles:
            messagebox.showwarning("Настройки сохранены, но работать нечем", "\n".join(troubles))
        else:
            messagebox.showinfo("Готово", f"Настройки сохранены:\n{path}")
        if on_save is not None:
            on_save(fresh)
        root.destroy()

    tk.Button(root, text="Сохранить", command=save).grid(row=base_row + len(FLAGS) + 1, column=1, sticky="w", padx=10, pady=14)
    tk.Button(root, text="Закрыть", command=root.destroy).grid(row=base_row + len(FLAGS) + 1, column=2, sticky="w", pady=14)
    root.columnconfigure(1, weight=1)
    root.mainloop()


class Tray:
    """Значок в трее. Без `pystray` тихо ничего не делает."""

    def __init__(self, settings, stop: threading.Event, reload_settings=None) -> None:
        self.settings = settings
        self._stop = stop
        self._reload = reload_settings
        self._icon = None

    def notify(self, title: str, text: str) -> None:
        if self._icon is None:
            return
        try:
            self._icon.notify(text, title)
        except Exception:  # noqa: BLE001
            logger.debug("Подсказка трея не показана", exc_info=True)

    def run(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            logger.info("Значок в трее недоступен: нет pystray или Pillow")
            self._stop.wait()
            return
        image = Image.new("RGB", (64, 64), "#1f6feb")
        ImageDraw.Draw(image).text((18, 22), "КРО", fill="white")

        def open_pages(_icon=None, _item=None) -> None:
            webbrowser.open(str(self.settings.server_url or "").rstrip("/") + "/")

        def open_settings(_icon=None, _item=None) -> None:
            settings_window(self.settings, on_save=self._reload)

        def quit_all(icon=None, _item=None) -> None:
            self._stop.set()
            if icon is not None:
                icon.stop()

        self._icon = pystray.Icon(
            "excelkro-companion",
            image,
            "Компаньон ExcelKRO",
            menu=pystray.Menu(
                pystray.MenuItem("Открыть страницу сверок", open_pages, default=True),
                pystray.MenuItem("Настройки…", open_settings),
                pystray.MenuItem("Выйти", quit_all),
            ),
        )
        self._icon.run()
