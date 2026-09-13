"""Диагностика справочников: что именно видит разборщик в загруженных книгах.

Скрипт не меняет файлы. Он печатает:

- есть ли местные копии книг и какого они размера;
- какие листы нашлись в каждой книге;
- сколько складов, визитов и фамилий разобрано;
- примеры разобранных складов и дат;
- разбор одной пары «склад + дата», если её передать ключами --store и --date.

Запуск на сервере:

    cd /opt/excelkro
    venv/bin/python tools/refs_doctor.py
    venv/bin/python tools/refs_doctor.py --store "Ленина 5" --date 12.09.2026
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path


def _add_project_root() -> None:
    """Делает пакет app видимым, откуда бы скрипт ни запускали."""
    candidates = [Path(__file__).resolve().parents[1], Path("/opt/excelkro")]
    for folder in candidates:
        if (folder / "app" / "__init__.py").is_file():
            sys.path.insert(0, str(folder))
            return


_add_project_root()

import openpyxl  # noqa: E402

from app.config import Settings  # noqa: E402
from app.core import refs  # noqa: E402
from app.core.refs_sync import local_paths  # noqa: E402

DATE_SHAPES = ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d")


def parse_day(text: str) -> dt.date | None:
    for shape in DATE_SHAPES:
        try:
            return dt.datetime.strptime(text.strip(), shape).date()
        except ValueError:
            continue
    return None


def show_file(path: Path, title: str) -> None:
    if not path.is_file():
        print(f"{title}: файла нет ({path})")
        return
    size_mb = round(path.stat().st_size / 1048576, 2)
    print(f"{title}: {path} — {size_mb} МБ")
    try:
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as error:  # noqa: BLE001
        print(f"  книга не открывается: {type(error).__name__}: {error}")
        return
    try:
        print(f"  листы: {book.sheetnames}")
    finally:
        book.close()


def show_planning(books: refs.RefsBooks) -> None:
    print(f"\nПланирование: складов {len(books.plans)}")
    for store in sorted(books.plans)[:10]:
        weeks = books.plans[store]
        first = weeks[0]
        print(
            f"  {store}: недель {len(weeks)}, пример "
            f"{first[0]}–{first[1]} → причина «{first[2]}», администратор «{first[3]}»"
        )
    if not books.plans:
        print("  ПУСТО: причина инвентаризации определяться не будет.")
        print("  Проверьте: названия листов, строку 2 с неделями вида «Январь 10-16»,")
        print("  столбец 2 с названиями магазинов, данные с третьей строки.")


def show_schedule(books: refs.RefsBooks) -> None:
    print(f"\nГрафик: визитов {len(books.visits)}, фамилий с полным ФИО {len(books.by_surname)}")
    for key in sorted(books.visits, key=lambda item: (item[1], item[0]))[:10]:
        admin, people = books.visits[key]
        print(f"  {key[1]} {key[0]}: администратор «{admin}», ревизоры {list(people)}")
    if not books.visits:
        print("  ПУСТО: администратор и ревизоры определяться не будут.")
        print(f"  Проверьте лист «{refs.SCHEDULE_SHEET}»: даты в первом столбце,")
        print("  шапку с фамилиями администраторов (нужно не меньше трёх),")
        print("  в ячейке — магазин, а под ним фамилии ревизоров.")
    days = sorted({day for _, day in books.visits})
    if days:
        print(f"  диапазон дат в графике: {days[0]} — {days[-1]}")


def show_lookup(books: refs.RefsBooks, store: str, day: dt.date, settings: Settings) -> None:
    key = refs.normalize_store(store)
    print(f"\nРазбор пары: склад «{store}» → ключ «{key}», дата {day}")

    for title, names in (
        ("планирование", sorted(books.plans)),
        ("график", sorted({name for name, _ in books.visits})),
    ):
        if not names:
            print(f"  {title}: складов нет")
            continue
        scored = sorted(
            ((refs.similarity(key, name), name) for name in names),
            reverse=True,
        )[:5]
        print(f"  {title}: похожие склады {[(name, round(score, 3)) for score, name in scored]}")
        best_score = scored[0][0]
        if best_score < settings.refs_match_min_score:
            print(
                f"    ниже порога REFS_MATCH_MIN_SCORE={settings.refs_match_min_score}:"
                " склад не будет найден"
            )

    info = refs.lookup(
        store,
        day,
        books,
        checker=settings.default_checker,
        min_score=settings.refs_match_min_score,
        days_around=settings.refs_days_around,
    )
    print("  итог:")
    print(f"    причина: {info.reason or 'не определена'}")
    print(f"    администратор: {info.admin or 'не определён'}")
    print(f"    проверяющий: {info.checker or '—'}")
    print(f"    ревизоры: {info.auditors_text or 'не определены'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Диагностика справочников")
    parser.add_argument("--store", default="", help="название склада как в имени файла сверки")
    parser.add_argument("--date", default="", help="дата инвентаризации, например 12.09.2026")
    args = parser.parse_args()

    settings = Settings.load()
    planning, schedule = local_paths(settings.refs_dir)

    print(f"Папка справочников: {settings.refs_dir}")
    print(f"Порог схожести складов: {settings.refs_match_min_score}")
    print(f"Допустимый сдвиг даты: {settings.refs_days_around} дн.\n")
    show_file(planning, "Книга планирования")
    show_file(schedule, "Книга графика")

    books = refs.load_books(str(planning), str(schedule), force=True)
    show_planning(books)
    show_schedule(books)

    if args.store or args.date:
        day = parse_day(args.date) if args.date else None
        if args.store and day is None:
            print("\nНужна дата в виде 12.09.2026: без неё разбор пары не делается.")
        elif day is not None:
            show_lookup(books, args.store, day, settings)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
