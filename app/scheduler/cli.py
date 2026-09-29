"""Запуск генератора з командного рядка.

    python -m app.scheduler.cli info/навантаження.xlsx -o розклад.xlsx --time 120
    python -m app.scheduler.cli info/навантаження.xlsx --date 2026-09-26   # + розклад на дату
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from datetime import date

from . import Semester, SolverConfig, TeacherRule, generate
from .exporter import build_day_workbook, day_title, export_xlsx
from .validator import summary


def load_rules(path: str) -> list[TeacherRule]:
    with open(path, encoding="utf-8") as f:
        return [TeacherRule.from_dict(r) for r in json.load(f)]


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
    parser.add_argument("--rules", help="JSON з вподобаннями викладачів (config/teacher_rules.json)")
    parser.add_argument("--hint", help="попередній розклад (schedule.pkl) як стартова точка")
    parser.add_argument("--stability", type=int, default=3, help="штраф за кожну пару, зрушену відносно --hint")
    parser.add_argument("--max-late-days", type=int, default=1, help="днів на тиждень без першої пари (5 — правило вимкнено)")
    args = parser.parse_args(argv)

    def progress(info):
        print(f"  етап {info['stage']}, розв'язок #{info['solutions']}: штраф {info['objective']:.0f} "
              f"({info['elapsed']} с)", flush=True)

    semester = Semester(args.start, args.end, args.first_week, saturdays=not args.no_saturdays)
    config = SolverConfig(time_limit=args.time, workers=args.workers, w_stability=args.stability,
                          max_late_days=args.max_late_days)
    rules = load_rules(args.rules) if args.rules else []
    hint = None
    if args.hint:
        with open(args.hint, "rb") as f:
            hint = pickle.load(f).placements
    schedule = generate(args.input, config, progress, semester=semester, rules=rules, hint=hint)
    print(f"Статус: {schedule.status}, штраф: {schedule.objective}, час: {schedule.solve_seconds} с")
    for rule, n in summary(schedule.issues).items():
        print(f"  {rule}: {n}")
    if not schedule.placements:
        return 1
    export_xlsx(schedule, args.output)
    with open(args.output.rsplit(".", 1)[0] + ".pkl", "wb") as f:
        pickle.dump(schedule, f)
    print(f"Збережено: {args.output}")
    if args.date:
        name = f"{day_title(schedule, args.date)}.xlsx"
        build_day_workbook(schedule, args.date).save(name)
        print(f"Збережено: {name} — {semester.describe(args.date)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
