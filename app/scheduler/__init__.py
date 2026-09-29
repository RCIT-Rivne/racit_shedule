"""Генератор розкладу коледжу."""
from __future__ import annotations

from pathlib import Path

from .loader import InputError, load_workbook
from .models import Issue, Placement, ProblemData, Schedule, TeacherRule
from .rooms import assign_rooms
from .semester import Semester
from .solver import ProgressFn, SolverConfig, solve
from .validator import precheck, validate

__all__ = ["InputError", "Semester", "SolverConfig", "TeacherRule", "generate", "load_workbook"]


def entries_to_placements(data: ProblemData, entries: list[dict]) -> list[Placement]:
    """Записи опублікованого розкладу -> Placement для поточного навантаження
    (зіставлення за групою, дисципліною й викладачами)."""
    by_key = {(l.group, l.subject, tuple(l.teachers)): l.id for l in data.lessons}
    out = []
    for e in entries:
        lid = by_key.get((e["group"], e["subject"], tuple(e["teachers"])))
        if lid is not None:
            out.append(Placement(lid, e["week"], e["day"], e["pair"]))
    return out


def generate(
    path: str | Path,
    config: SolverConfig | None = None,
    progress: ProgressFn | None = None,
    semester: Semester | None = None,
    rules: list[TeacherRule] | None = None,
    hint: list[Placement] | None = None,
    hint_entries: list[dict] | None = None,
) -> Schedule:
    """Повний цикл: читання файлу -> розв'язок -> аудиторії -> перевірка.

    Будується розклад пн–пт на два тижні (чисельник/знаменник); семестр
    визначає лише календар (суботи за ротацією, тиждень за датою).
    """
    config = config or SolverConfig()
    data: ProblemData = load_workbook(path)
    data.rules = list(rules or [])
    unknown = sorted({t for r in data.rules for t in r.teachers} - set(data.teachers))
    if unknown:
        data.warnings.append(f"У правилах є викладачі, яких немає в навантаженні: {', '.join(unknown)}")
    if hint_entries:
        hint = entries_to_placements(data, hint_entries)
    schedule = solve(data, config, progress, hint=hint)
    schedule.semester = semester or Semester.default()
    if schedule.placements:
        assign_rooms(schedule)
        schedule.issues = validate(
            schedule,
            min_pairs=config.min_pairs_per_day,
            max_pairs=config.max_pairs_per_day,
            max_teacher=config.max_teacher_pairs_per_day,
            max_late_days=config.max_late_days,
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
