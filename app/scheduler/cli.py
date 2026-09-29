"""Запуск генератора з командного рядка.

    python -m app.scheduler.cli info/навантаження.xlsx -o розклад.xlsx --time 120
    python -m app.scheduler.cli info/навантаження.xlsx --date 2026-09-26   # + розклад на дату
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from . import Semester, SolverConfig, generate
from .exporter import build_day_workbook, day_title, export_xlsx
from .validator import summary


def main(argv: list[str] | None = None) -> int:
    default = Semester.default()
    parser = argparse.ArgumentParser(description="Генерація розкладу коледжу")
    parser.add_argument("input", help="xlsx з аркушами «Навантаження» та «аудиторії»")
    parser.add_argument("-o", "--output", default="schedule.xlsx")
    parser.add_argument("--time", type=float, default=120, help="ліміт часу розв'язувача, с")
    parser.add_argument("--workers", type=int, default=0, help="потоків розв'язувача, 0 — усі ядра")
    parser.add_argument("--start", type=date.fromisoformat, default=default.start, help="початок семестру")
    parser.add_argument("--end", type=date.fromisoformat, default=default.end, help="кінець семестру")
    parser.add_argument("--first-week", type=int, choices=(0, 1), default=0, help="0 — чисельник, 1 — знаменник")
    parser.add_argument("--no-saturdays", action="store_true", help="суботи не навчальні")
    parser.add_argument("--date", type=date.fromisoformat, help="також зберегти розклад на цю дату")
    args = parser.parse_args(argv)

    def progress(info):
        print(f"  етап {info['stage']}, розв'язок #{info['solutions']}: штраф {info['objective']:.0f} "
              f"({info['elapsed']} с)", flush=True)

    semester = Semester(args.start, args.end, args.first_week, saturdays=not args.no_saturdays)
    config = SolverConfig(time_limit=args.time, workers=args.workers)
    schedule = generate(args.input, config, progress, semester=semester)
    print(f"Статус: {schedule.status}, штраф: {schedule.objective}, час: {schedule.solve_seconds} с")
    for rule, n in summary(schedule.issues).items():
        print(f"  {rule}: {n}")
    if not schedule.placements:
        return 1
    export_xlsx(schedule, args.output)
    print(f"Збережено: {args.output}")
    if args.date:
        name = f"{day_title(schedule, args.date)}.xlsx"
        build_day_workbook(schedule, args.date).save(name)
        print(f"Збережено: {name} — {semester.describe(args.date)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
