"""Проверка разбора справочников без запуска сайта.

Показывает, что программа реально вычитала из книг планирования и графика:
сколько магазинов, недель, записей распределения и ФИО, а для конкретной
пары «склад + дата» — что попадёт в файл сверки и почему.

Запуск:

    python -m tools.refs_doctor
    python -m tools.refs_doctor --warehouse "ОхтаМол СМА" --date 12.08.2026
    python -m tools.refs_doctor --planning /path/plan.xlsx --schedule /path/graf.xlsx
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.core import refs  # noqa: E402


def _date(text: str) -> dt.date:
    day = refs._as_date(text)
    if day is None:
        raise argparse.ArgumentTypeError(f"Не похоже на дату: {text}")
    return day


def _paths(settings: Settings, planning: str, schedule: str) -> tuple[str, str]:
    refs_dir = Path(settings.refs_dir)
    plan = planning or str(refs_dir / "planning.xlsx")
    graph = schedule or str(refs_dir / "schedule.xlsx")
    return plan, graph


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка разбора справочников")
    parser.add_argument("--planning", default="", help="Книга планирования (.xlsx)")
    parser.add_argument("--schedule", default="", help="Книга графика (.xlsx)")
    parser.add_argument("--warehouse", default="", help="Название склада из сверки")
    parser.add_argument("--date", type=_date, default=None, help="Дата сверки")
    parser.add_argument(
        "--stores", type=int, default=0, help="Показать N названий складов из книг"
    )
    args = parser.parse_args()

    settings = Settings.load()
    planning, schedule = _paths(settings, args.planning, args.schedule)
    for path in (planning, schedule):
        print(f"Файл: {path}{'' if Path(path).is_file() else '  — НЕ НАЙДЕН'}")

    books = refs.load_books(planning, schedule, force=True)
    weeks = sum(len(entries) for entries in books.plans.values())
    print(
        f"Прочитано: магазинов в планировании {len(books.plans)}, "
        f"записей причин {weeks}, записей распределения {len(books.visits)}, "
        f"фамилий с полным ФИО {len(books.by_surname)}"
    )
    with_people = sum(1 for _, people in books.visits.values() if people)
    print(f"Записей распределения с ревизорами: {with_people} из {len(books.visits)}")

    if args.stores:
        print("\nНазвания складов в планировании:")
        for name in sorted(books.plans)[: args.stores]:
            print(f"  {name}")
        print("Названия складов в графике:")
        for name in sorted({store for store, _ in books.visits})[: args.stores]:
            print(f"  {name}")

    if args.warehouse and args.date:
        info = refs.lookup(
            args.warehouse,
            args.date,
            books,
            checker=settings.default_checker,
            min_score=settings.refs_match_min_score,
            days_around=settings.refs_days_around,
            confirm_min_score=settings.refs_confirm_min_score,
        )
        print(f"\nПоиск: {args.warehouse} на {args.date:%d.%m.%Y}")
        print(f"  Причина:       {info.reason or '— не найдена'}")
        print(f"  Администратор: {info.admin or '— не найден'}")
        print(f"  Проверил:      {info.checker or '—'}")
        print(f"  Ревизоры:      {info.auditors_text or '— не найдены'}")
        print(f"  Уверенность:   {info.confidence:.2f}, сдвиг даты {info.day_shift:+d} дн.")
        print(f"  Требует подтверждения: {'да' if info.uncertain else 'нет'}")
        for line in info.notes:
            print(f"    · {line}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
