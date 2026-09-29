"""Показники якості розкладу (для порівняння варіантів і з ручним розкладом)."""
from __future__ import annotations

from collections import Counter, defaultdict

from .models import Schedule


def quality(schedule: Schedule) -> dict:
    group_days: dict[tuple, list[int]] = defaultdict(list)
    teacher_days: dict[tuple, list[int]] = defaultdict(list)
    teacher_rooms: dict[str, set] = defaultdict(set)
    for p in schedule.placements:
        l = schedule.lesson(p.lesson_id)
        group_days[l.group, p.week, p.day].append(p.pair)
        for i, t in enumerate(l.teachers):
            teacher_days[t, p.week, p.day].append(p.pair)
            if i < len(p.rooms):
                teacher_rooms[t].add(p.rooms[i])

    def windows(pairs: list[int]) -> int:
        return max(pairs) - min(pairs) + 1 - len(pairs)

    n_groups = len(schedule.data.groups)
    n_days = len(schedule.data.days) * 2
    teacher_windows = [windows(ps) for ps in teacher_days.values()]
    teacher_load = Counter(len(ps) for ps in teacher_days.values())
    rooms_per_teacher = [len(r) for r in teacher_rooms.values()]
    return {
        "Вільних днів у груп": n_groups * n_days - len(group_days),
        "Днів груп з вікнами": sum(1 for ps in group_days.values() if windows(ps)),
        "Пар на день у груп": dict(sorted(Counter(len(ps) for ps in group_days.values()).items())),
        "Початок дня у груп (пара)": dict(sorted(Counter(min(ps) for ps in group_days.values()).items())),
        "Днів викладачів з вікнами": f"{sum(1 for w in teacher_windows if w)} з {len(teacher_windows)}",
        "Сумарно пар-вікон викладачів": sum(teacher_windows),
        "Пар на день у викладачів": dict(sorted(teacher_load.items())),
        "Аудиторій на викладача (середнє)": round(sum(rooms_per_teacher) / max(len(rooms_per_teacher), 1), 2),
    }
