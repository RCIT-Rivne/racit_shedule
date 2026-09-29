"""Генератор розкладу коледжу."""
from __future__ import annotations

from pathlib import Path

from .loader import InputError, load_workbook
from .models import Issue, ProblemData, Schedule
from .rooms import assign_rooms
from .semester import Semester
from .solver import ProgressFn, SolverConfig, solve
from .validator import precheck, validate

__all__ = ["InputError", "Semester", "SolverConfig", "generate", "load_workbook"]


def generate(
    path: str | Path,
    config: SolverConfig | None = None,
    progress: ProgressFn | None = None,
    semester: Semester | None = None,
) -> Schedule:
    """Повний цикл: читання файлу -> розв'язок -> аудиторії -> перевірка.

    Будується розклад пн–пт на два тижні (чисельник/знаменник); семестр
    визначає лише календар (суботи за ротацією, тиждень за датою).
    """
    config = config or SolverConfig()
    data: ProblemData = load_workbook(path)
    schedule = solve(data, config, progress)
    schedule.semester = semester or Semester.default()
    if schedule.placements:
        assign_rooms(schedule)
        schedule.issues = validate(
            schedule,
            min_pairs=config.min_pairs_per_day,
            max_pairs=config.max_pairs_per_day,
            max_teacher=config.max_teacher_pairs_per_day,
        )
    else:
        reasons = precheck(
            data,
            n_days=len(data.days),
            max_group=config.max_pairs_per_day,
            max_teacher=config.max_teacher_pairs_per_day,
        )
        hint = "Причини нижче." if reasons else "Спробуйте збільшити час пошуку."
        schedule.issues = [
            Issue("error", "Розв'язок", f"Розклад не знайдено (статус {schedule.status}). {hint}")
        ] + reasons
    schedule.issues = [Issue("info", "Вхідні дані", w) for w in data.warnings] + schedule.issues
    return schedule
