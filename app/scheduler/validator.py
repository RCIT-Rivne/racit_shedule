"""Незалежна перевірка готового розкладу на відповідність правилам."""
from __future__ import annotations

from collections import Counter, defaultdict

from .models import WEEKS, Issue, Schedule


def validate(schedule: Schedule, min_pairs: int = 3, max_pairs: int = 4, max_teacher: int = 4) -> list[Issue]:
    issues: list[Issue] = []
    data = schedule.data

    def _where(week: int, day: int) -> str:
        return f"{WEEKS[week].lower()}, {data.days[day]}"

    group_slots: dict[tuple, list] = defaultdict(list)
    teacher_slots: dict[tuple, list] = defaultdict(list)
    room_slots: dict[tuple, list] = defaultdict(list)
    counts: Counter = Counter()
    shared_rooms = {r.name for r in data.rooms if r.shared}

    for p in schedule.placements:
        l = schedule.lesson(p.lesson_id)
        group_slots[l.group, p.week, p.day, p.pair].append(l)
        for t in l.teachers:
            teacher_slots[t, p.week, p.day, p.pair].append(l)
        for r in p.rooms:
            if r not in shared_rooms:
                room_slots[r, p.week, p.day, p.pair].append(l)
        counts[l.id, p.week] += 1

    for (t, w, d, pair), ls in teacher_slots.items():
        if len(ls) > 1:
            groups = ", ".join(l.group for l in ls)
            issues.append(Issue("error", "Накладання викладача", f"{t}: {_where(w, d)}, пара {pair} — {groups}"))
    for (g, w, d, pair), ls in group_slots.items():
        if len(ls) > 1:
            issues.append(Issue("error", "Накладання в групі", f"{g}: {_where(w, d)}, пара {pair}"))
    for (r, w, d, pair), ls in room_slots.items():
        if len(ls) > 1:
            groups = ", ".join(l.group for l in ls)
            issues.append(Issue("error", "Накладання аудиторії", f"ауд. {r}: {_where(w, d)}, пара {pair} — {groups}"))

    # Навантаження груп по днях, вікна, однакові дисципліни.
    by_group_day: dict[tuple, list] = defaultdict(list)
    for (g, w, d, pair), ls in group_slots.items():
        by_group_day[g, w, d].append((pair, ls[0]))
    for group in data.groups:
        own_pairs = set(group.pair_numbers())
        for w in range(len(WEEKS)):
            for d in range(len(data.days)):
                items = sorted(by_group_day.get((group.name, w, d), []), key=lambda x: x[0])
                n = len(items)
                if n and not min_pairs <= n <= max_pairs:
                    issues.append(
                        Issue("warning", "Кількість пар у групи", f"{group.name}: {_where(w, d)} — {n} пар(и)")
                    )
                pairs = [p for p, _ in items]
                if pairs and pairs[-1] - pairs[0] + 1 != len(pairs):
                    issues.append(Issue("warning", "Вікна", f"{group.name}: {_where(w, d)} — пари {pairs}"))
                outside = [p for p in pairs if p not in own_pairs]
                if outside:
                    issues.append(
                        Issue("warning", "Перехід між змінами",
                              f"{group.name} ({group.shift} зміна): {_where(w, d)} — пара {outside}")
                    )
                subj = Counter(l.subject_key for _, l in items)
                for s, c in subj.items():
                    if c > 1:
                        name = next(l.subject for _, l in items if l.subject_key == s)
                        issues.append(
                            Issue("warning", "Однакові дисципліни", f"{group.name}: {_where(w, d)} — «{name}» ×{c}")
                        )

    # Навантаження та вікна викладачів по днях.
    teacher_day: Counter = Counter()
    teacher_pairs: dict[tuple, list] = defaultdict(list)
    for (t, w, d, pair), ls in teacher_slots.items():
        teacher_day[t, w, d] += 1
        teacher_pairs[t, w, d].append(pair)
    windows = sum(max(ps) - min(ps) + 1 - len(ps) for ps in teacher_pairs.values())
    window_days = sum(1 for ps in teacher_pairs.values() if max(ps) - min(ps) + 1 != len(ps))
    if windows:
        issues.append(
            Issue(
                "info",
                "Вікна викладачів",
                f"{window_days} з {len(teacher_pairs)} робочих днів викладачів мають вікна, "
                f"сумарно {windows} пар (див. аркуш «Викладачі»)",
            )
        )
    for (t, w, d), n in teacher_day.items():
        if n > max_teacher:
            issues.append(Issue("error", "Навантаження викладача", f"{t}: {_where(w, d)} — {n} пар"))

    # Відповідність навчальному плану.
    for l in data.lessons:
        lo, hi = l.week_bounds
        c0, c1 = counts[l.id, 0], counts[l.id, 1]
        if c0 + c1 != l.total_two_weeks or not (lo <= c0 <= hi and lo <= c1 <= hi):
            issues.append(
                Issue(
                    "error",
                    "Навчальний план",
                    f"{l.group} / {l.subject}: план {l.per_week:g} пар/тиж, поставлено {c0} + {c1}",
                )
            )

    for p in schedule.placements:
        if data.rooms and not p.rooms:
            l = schedule.lesson(p.lesson_id)
            issues.append(
                Issue("warning", "Аудиторії", f"{l.group} / {l.subject}: {_where(p.week, p.day)}, пара {p.pair} без аудиторії")
            )
    return issues


def precheck(data, n_days: int, max_group: int = 4, max_teacher: int = 4) -> list[Issue]:
    """Очевидні причини, з яких розклад не може існувати (перевірка до пошуку)."""
    issues = []
    teacher_week: dict[tuple, int] = defaultdict(int)
    group_week: dict[tuple, int] = defaultdict(int)
    for l in data.lessons:
        lo, hi = l.week_bounds
        for t in l.teachers:
            teacher_week[t, "min"] += lo
            teacher_week[t, "max"] += hi
        group_week[l.group, "min"] += lo
    for (t, kind), n in teacher_week.items():
        if kind == "min" and n > max_teacher * n_days:
            issues.append(
                Issue("error", "Навантаження викладача",
                      f"{t}: {n} пар на тиждень, а максимум {max_teacher} × {n_days} днів = {max_teacher * n_days}")
            )
    for (g, _), n in group_week.items():
        if n > max_group * n_days:
            issues.append(
                Issue("error", "Навантаження групи",
                      f"{g}: {n} пар на тиждень, а максимум {max_group} × {n_days} днів = {max_group * n_days}")
            )
    return issues


def summary(issues: list[Issue]) -> dict[str, int]:
    out: Counter = Counter()
    for i in issues:
        out[i.rule] += 1
    return dict(out)
