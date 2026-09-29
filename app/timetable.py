"""Розклад на конкретну дату: постійний розклад + календар + практика + зміни.

Постійний розклад зберігає тиждень пн–пт у двох варіантах (чисельник і
знаменник). Дата перетворюється на (день, тиждень) календарем семестру
(суботи — за ротацією), далі поверх накладаються практика груп, зміни на
цю дату та відсутності викладачів.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select

from .db import Absence, Change, Practice, TeacherConstraint, Timetable, TimetableEntry, session
from .scheduler.models import TOTAL_PAIRS, uk_sort_key
from .scheduler.semester import Semester

# Розклад дзвінків коледжу: пара — 80 хв, велика перерва після 2-ї пари.
# Початки 1–6 пар узгоджені з електронним журналом; 7-ма — за тим самим ритмом.
BELLS = {
    1: ("08:30", "09:50"),
    2: ("10:05", "11:25"),
    3: ("11:50", "13:10"),
    4: ("13:25", "14:45"),
    5: ("15:00", "16:20"),
    6: ("16:35", "17:55"),
    7: ("18:10", "19:30"),
}
SHARED_ROOMS = {"спортзал"}


@dataclass
class Slot:
    """Одна пара однієї групи на конкретну дату (після всіх змін)."""

    date: date
    pair: int
    group: str
    subject: str
    teachers: list[str]
    rooms: list[str]
    status: str = "regular"  # regular | substitute | room | cancelled | added | practice
    original: TimetableEntry | None = None
    change: Change | None = None
    missing: list[str] = field(default_factory=list)  # відсутні викладачі без рішення

    @property
    def time(self) -> tuple[str, str]:
        return BELLS.get(self.pair, ("", ""))

    @property
    def is_active(self) -> bool:
        return self.status not in ("cancelled", "practice")

    @property
    def is_changed(self) -> bool:
        return self.status not in ("regular", "practice")


def semester_of(tt: Timetable) -> Semester:
    return Semester(tt.semester_start, tt.semester_end, tt.first_week, tt.saturdays)


_entries_cache: dict[int, dict] = {}


def clear_cache() -> None:
    _entries_cache.clear()


def _entries(tt: Timetable) -> dict[tuple[int, int], list[TimetableEntry]]:
    """{(тиждень, день): [записи]} — кеш на опублікований розклад."""
    key = tt.id
    cached = _entries_cache.get(key)
    if cached is None or cached["published_at"] != tt.published_at:
        by_day: dict[tuple[int, int], list[TimetableEntry]] = defaultdict(list)
        for e in session.scalars(select(TimetableEntry).where(TimetableEntry.timetable_id == tt.id)):
            by_day[e.week, e.day].append(e)
        cached = _entries_cache[key] = {"published_at": tt.published_at, "by_day": by_day}
    return cached["by_day"]


def groups_of(tt: Timetable) -> list[dict]:
    """[{name, course}] у порядку з розкладу."""
    seen: dict[str, int] = {}
    for entries in _entries(tt).values():
        for e in entries:
            seen.setdefault(e.group, e.course)
    order = {name: i for i, name in enumerate(tt_group_order(tt))}
    return sorted(({"name": g, "course": c} for g, c in seen.items()), key=lambda g: (g["course"], order.get(g["name"], 999)))


def tt_group_order(tt: Timetable) -> list[str]:
    ids = session.execute(
        select(TimetableEntry.group).where(TimetableEntry.timetable_id == tt.id).order_by(TimetableEntry.id)
    ).scalars()
    return list(dict.fromkeys(ids))


def course_pairs(tt: Timetable) -> set[tuple[int, str]]:
    """{(пара, група)} — які пари в постійному розкладі бувають у групи."""
    return {(e.pair, e.group) for entries in _entries(tt).values() for e in entries}


def teachers_of(tt: Timetable) -> list[str]:
    names = {t for entries in _entries(tt).values() for e in entries for t in e.teachers}
    return sorted(names, key=uk_sort_key)


def rooms_of(tt: Timetable) -> list[str]:
    names = {r for entries in _entries(tt).values() for e in entries for r in e.rooms}
    return sorted(names, key=lambda r: (not r.isdigit(), int(r) if r.isdigit() else 0, r))


def day_slots(tt: Timetable, day: date) -> list[Slot]:
    """Усі пари всіх груп на дату. Порожньо — вихідний або поза семестром."""
    resolved = semester_of(tt).resolve(day)
    changes = session.scalars(select(Change).where(Change.date == day).order_by(Change.id)).all()
    practices = session.scalars(
        select(Practice).where(Practice.date_from <= day, Practice.date_to >= day)
    ).all()
    absences = session.scalars(
        select(Absence).where(Absence.date_from <= day, Absence.date_to >= day)
    ).all()
    on_practice = {p.group for p in practices}

    slots: dict[tuple[str, int], Slot] = {}
    if resolved is not None:
        d, week = resolved
        for e in _entries(tt).get((week, d), []):
            slot = Slot(day, e.pair, e.group, e.subject, list(e.teachers), list(e.rooms), original=e)
            if e.group in on_practice:
                slot.status = "practice"
            slots[e.group, e.pair] = slot

    for c in changes:  # остання зміна для пари перемагає
        slot = slots.get((c.group, c.pair))
        if c.kind == Change.ADD or slot is None:
            slots[c.group, c.pair] = Slot(
                day, c.pair, c.group, c.subject or "", list(c.teachers), list(c.rooms),
                status="added", original=slot.original if slot else None, change=c,
            )
            continue
        slot.change = c
        if c.kind == Change.CANCEL:
            slot.status = "cancelled"
        elif c.kind == Change.ROOM:
            slot.status = "room"
            slot.rooms = list(c.rooms)
        elif c.kind == Change.SUBSTITUTE:
            slot.status = "substitute"
            slot.teachers = list(c.teachers)
            slot.subject = c.subject or slot.subject
            if c.rooms:
                slot.rooms = list(c.rooms)

    for slot in slots.values():
        if slot.is_active:
            slot.missing = [t for t in slot.teachers if any(a.teacher == t and a.covers(day, slot.pair) for a in absences)]
    return sorted(slots.values(), key=lambda s: (s.pair, s.group))


def for_group(slots: list[Slot], group: str) -> list[Slot]:
    return [s for s in slots if s.group == group]


def for_teacher(slots: list[Slot], teacher: str) -> list[Slot]:
    """Пари викладача, включно з тими, де його замінили (щоб він це бачив)."""
    out = []
    for s in slots:
        if teacher in s.teachers or (s.original is not None and teacher in s.original.teachers):
            out.append(s)
    return out


def week_dates(day: date, saturdays: bool = True) -> list[date]:
    monday = day - timedelta(days=day.weekday())
    return [monday + timedelta(days=i) for i in range(6 if saturdays else 5)]


# --- Перевірки та підказки для редагування -------------------------------------

def teacher_unavailable(teacher: str, day: date, pair: int) -> str | None:
    """Причина, чому викладач не може цієї пари (відсутність або постійне обмеження)."""
    for a in session.scalars(select(Absence).where(Absence.teacher == teacher, Absence.date_from <= day, Absence.date_to >= day)):
        if a.covers(day, pair):
            return f"відсутній: {Absence.REASONS.get(a.reason, a.reason).lower()}"
    if day.weekday() < 5:
        for c in session.scalars(select(TeacherConstraint).where(TeacherConstraint.teacher == teacher, TeacherConstraint.weekday == day.weekday())):
            if not c.pairs or pair in c.pairs:
                return "постійне обмеження в цей день"
    return None


def conflicts(slots: list[Slot], group: str, pair: int, teachers: list[str], rooms: list[str]) -> list[str]:
    """Накладання, якщо поставити цих викладачів/аудиторії групі на пару."""
    out = []
    for s in slots:
        if s.pair != pair or s.group == group or not s.is_active:
            continue
        for t in teachers:
            if t in s.teachers:
                out.append(f"{t} уже веде пару в {s.group}")
        for r in rooms:
            if r in s.rooms and r not in SHARED_ROOMS:
                out.append(f"ауд. {r} зайнята групою {s.group}")
    if slots:
        day = slots[0].date
        for t in teachers:
            reason = teacher_unavailable(t, day, pair)
            if reason:
                out.append(f"{t}: {reason}")
            load = sum(1 for s in slots if s.is_active and t in s.teachers and not (s.group == group and s.pair == pair))
            if load >= 4:
                out.append(f"{t}: уже {load} пари цього дня")
    return out


_COMMISSIONS: dict[str, str] | None = None
COMMISSIONS_FILE = Path(__file__).resolve().parent.parent / "config" / "commissions.json"


def commission_of(teacher: str) -> str | None:
    """Циклова комісія викладача (config/commissions.json, зібрано з сайту коледжу)."""
    global _COMMISSIONS
    if _COMMISSIONS is None:
        _COMMISSIONS = {}
        if COMMISSIONS_FILE.exists():
            data = json.loads(COMMISSIONS_FILE.read_text(encoding="utf-8"))
            for name, members in data.items():
                for t in members.get("site", []) + members.get("inferred_by_subjects", []):
                    _COMMISSIONS[t] = name
    return _COMMISSIONS.get(teacher)


def substitute_candidates(tt: Timetable, slots: list[Slot], slot: Slot, limit: int = 8) -> list[dict]:
    """Хто може замінити: вільний у цю пару, не відсутній, <4 пар за день.
    Вище — хто веде ту саму дисципліну, веде цій групі, має сусідню пару (без вікна)."""
    subject = (slot.original.subject if slot.original else slot.subject).lower()
    teaches_subject, teaches_group = set(), set()
    for entries in _entries(tt).values():
        for e in entries:
            if e.subject.lower() == subject:
                teaches_subject.update(e.teachers)
            if e.group == slot.group:
                teaches_group.update(e.teachers)

    busy = {t for s in slots if s.pair == slot.pair and s.is_active for t in s.teachers}
    load: dict[str, list[int]] = defaultdict(list)
    for s in slots:
        if s.is_active:
            for t in s.teachers:
                load[t].append(s.pair)

    exclude = set(slot.teachers) | set(slot.original.teachers if slot.original else [])
    commissions = {commission_of(t) for t in (slot.original.teachers if slot.original else slot.teachers)} - {None}
    out = []
    for t in teachers_of(tt):
        if t in busy or t in exclude or teacher_unavailable(t, slot.date, slot.pair) or len(load[t]) >= 4:
            continue
        pairs = load[t]
        adjacent = any(abs(p - slot.pair) == 1 for p in pairs)
        same_commission = commission_of(t) in commissions
        score = 3 * (t in teaches_subject) + 2 * (t in teaches_group) + 1.5 * same_commission + adjacent - (not pairs) * 0.5
        reasons = []
        if t in teaches_subject:
            reasons.append("веде цю дисципліну")
        if same_commission:
            reasons.append("та сама циклова комісія")
        if t in teaches_group:
            reasons.append("веде цій групі")
        if adjacent:
            reasons.append("має сусідню пару")
        elif not pairs:
            reasons.append("цього дня пар немає")
        out.append({"teacher": t, "score": score, "reasons": reasons, "load": len(pairs)})
    out.sort(key=lambda c: (-c["score"], c["load"], uk_sort_key(c["teacher"])))
    return out[:limit]


def free_rooms(tt: Timetable, slots: list[Slot], pair: int) -> list[str]:
    busy = {r for s in slots if s.pair == pair and s.is_active for r in s.rooms}
    return [r for r in rooms_of(tt) if r not in busy or r in SHARED_ROOMS]


def publish(schedule, name: str, source_job: str | None) -> Timetable:
    """Зберегти згенерований розклад як постійний і зробити його активним."""
    from .db import now

    sem = schedule.semester
    courses = {g.name: g.course for g in schedule.data.groups}
    tt = Timetable(
        name=name,
        source_job=source_job,
        semester_start=sem.start,
        semester_end=sem.end,
        first_week=sem.first_week,
        saturdays=sem.saturdays,
        is_active=True,
        published_at=now(),
    )
    for p in schedule.placements:
        lesson = schedule.lesson(p.lesson_id)
        tt.entries.append(
            TimetableEntry(
                group=lesson.group,
                course=courses.get(lesson.group, 0),
                subject=lesson.subject,
                teachers=list(lesson.teachers),
                rooms=list(p.rooms),
                week=p.week,
                day=p.day,
                pair=p.pair,
            )
        )
    for other in session.scalars(select(Timetable).where(Timetable.is_active.is_(True))):
        other.is_active = False
    session.add(tt)
    session.commit()
    return tt


PAIRS = list(range(1, TOTAL_PAIRS + 1))
