"""Запуск компаньона.

    python -m companion               — работа со значком в трее;
    python -m companion --без-трея  — только цикл работы и журнал;
    python -m companion --раз       — один проход и выход (удобно в проверках);
    python -m companion --настройки — только окно настроек.

Журнал лежит рядом с настройками и пишется с перезаписью по кругу,
чтобы не съесть диск на рабочем компьютере.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import threading

from .client import Client
from .config import Settings, default_dir
from .state import Store
from .ui import Tray, settings_window
from .worker import Companion

logger = logging.getLogger("companion")


def setup_log(folder) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        folder / "companion.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[handler, logging.StreamHandler()],
    )


def work(settings: Settings, store: Store, stop: threading.Event, notify=None, once: bool = False) -> None:
    """Цикл работы. Каждый проход строит своё соединение с сервером."""
    while True:
        try:
            with Client(settings) as client:
                report = Companion(settings, client, store, notify=notify).tick()
            if report["troubles"]:
                logger.warning("Настройки неполные: %s", "; ".join(report["troubles"]))
            else:
                logger.debug("Проход: %s", report)
        except Exception:  # noqa: BLE001
            # Программа живёт на рабочем компьютере сутками: любая
            # беда с сетью или диском не должна её ронять.
            logger.exception("Проход оборвался")
        if once or stop.wait(max(int(settings.poll_seconds or 20), 5)):
            return


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Компаньон ExcelKROAutoFormat")
    parser.add_argument("--раз", dest="once", action="store_true", help="один проход и выход")
    parser.add_argument("--без-трея", dest="no_tray", action="store_true", help="работать без значка в трее")
    parser.add_argument("--настройки", dest="setup", action="store_true", help="только окно настроек")
    args = parser.parse_args(argv)

    folder = default_dir()
    setup_log(folder)
    settings = Settings.load(folder)

    if args.setup:
        settings_window(settings)
        return 0

    store = Store(folder)
    stop = threading.Event()

    if args.no_tray or args.once:
        work(settings, store, stop, once=args.once)
        return 0

    box = {"settings": settings}
    tray = Tray(box["settings"], stop, reload_settings=lambda fresh: box.update(settings=fresh))
    thread = threading.Thread(
        target=lambda: work(box["settings"], store, stop, notify=tray.notify),
        name="companion-work",
        daemon=True,
    )
    thread.start()
    try:
        tray.run()
    finally:
        stop.set()
        thread.join(timeout=30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
