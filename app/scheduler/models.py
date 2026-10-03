"""Структури даних генератора розкладу."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

DAYS = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця"]
SATURDAY_NAME = "Субота"
WEEKS = ["Чисельник", "Знаменник"]
PAIRS_PER_SHIFT = 4
TOTAL_PAIRS = 7  # у коледжі пар 1–7: І зміна — 1–4, ІІ зміна — 5–7


UK_ALPHABET = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"
_UK_ORDER = {ch: i for i, ch in enumerate(UK_ALPHABET)}


def uk_sort_key(value: str) -> list[tuple[int, str]]:
    """Сортування за українським алфавітом (у Unicode «Є», «І», «Ї» стоять не на місці)."""
    return [(_UK_ORDER.get(ch, 100 + ord(ch)), ch) for ch in value.lower()]


def normalize_spaces(value: str) -> str:
    return " ".join(str(value).split())


def normalize_teacher(name: str) -> str:
    """'Сальчук А.В.' -> 'Сальчук А. В.' — щоб однакові викладачі збігались."""
    name = normalize_spaces(name)
    name = re.sub(r"\.(?=\S)", ". ", name)
    return name.strip()


def teacher_key(name: str) -> tuple[str, str] | None:
    """(прізвище, перша ініціала) для «Прізвище І. П.» та «Ім'я ПРІЗВИЩЕ»."""
    parts = name.replace(".", ". ").split()
    if len(parts) >= 2 and parts[1].endswith("."):
        return parts[0].lower(), parts[1][0].lower()
    if len(parts) == 2 and parts[1].isupper() and len(parts[1]) > 1:
        return parts[1].lower(), parts[0][0].lower()
    return None


def is_full_name(name: str) -> bool:
    """«Сергій ПАНДРАК» — ім'я та прізвище великими літерами, без ініціалів."""
    parts = name.split()
    return len(parts) == 2 and "." not in name and parts[1].isupper()


def course_of(group: str) -> int:
    m = re.search(r"-(\d+)/", group)
    return int(m.group(1)) if m else 0


@dataclass
class Lesson:
    """Один рядок навантаження: група + дисципліна + викладач(і)."""

    id: int
    group: str
    subject: str
    teachers: list[str]
    per_week: float
    shift: int

    @property
    def total_two_weeks(self) -> int:
        return int(round(self.per_week * 2))

    @property
    def week_bounds(self) -> tuple[int, int]:
        total = self.total_two_weeks
        return total // 2, math.ceil(total / 2)

    @property
    def is_fractional(self) -> bool:
        lo, hi = self.week_bounds
        return lo != hi

    @property
    def subject_key(self) -> str:
        return self.subject.lower()


@dataclass
class Room:
    name: str
    note: str = ""
    kind: str = "regular"  # regular | computer | programmers | lab | gym | range | reserve | other
    shared: bool = False  # кілька груп одночасно (спортзал)


@dataclass
class Group:
    name: str
    shift: int

    @property
    def course(self) -> int:
        return course_of(self.name)

    def pair_numbers(self, max_pair: int = TOTAL_PAIRS) -> list[int]:
        """Пари своєї зміни: І — 1–4, ІІ — 5–7 (обмежено max_pair)."""
        start = (self.shift - 1) * PAIRS_PER_SHIFT + 1
        return [p for p in range(start, start + PAIRS_PER_SHIFT) if p <= max_pair]

    def allowed_pairs(self, max_pair: int = TOTAL_PAIRS, cross_shift: bool = True) -> list[int]:
        """Пари своєї зміни + (якщо дозволено) одна сусідня пара іншої зміни."""
        own = self.pair_numbers(max_pair)
        if cross_shift:
            neighbour = own[-1] + 1 if self.shift == 1 else own[0] - 1
            if 1 <= neighbour <= max_pair:
                own = sorted(own + [neighbour])
        return own


@dataclass
class TeacherRule:
    """Вподобання/обмеження викладача (або групи викладачів, напр. «Адміністрація»).

    kind:
      unavailable — не ставити пари в дні `days` на пари `pairs` (None — усі);
      max_days    — не більше `value` робочих днів на тиждень (методичний день = 4);
      room        — пари викладача лише в аудиторії `room` (якщо задано `subject` —
                    лише заняття, у назві яких є цей текст).
    hard=True — правило не порушується ніколи; False — «за можливості» (штраф).
    """

    teachers: list[str]
    kind: str
    days: list[int] | None = None
    pairs: list[int] | None = None
    hard: bool = True
    value: int | None = None
    room: str | None = None
    subject: str | None = None
    label: str = ""

    KINDS = ("unavailable", "max_days", "room")

    def applies_to(self, lesson: Lesson, teacher: str | None = None) -> bool:
        """Чи стосується правило заняття (і конкретного викладача, якщо задано)."""
        if teacher is not None and teacher not in self.teachers:
            return False
        if teacher is None and not set(self.teachers) & set(lesson.teachers):
            return False
        return not self.subject or self.subject.lower() in lesson.subject.lower()

    def cells(self, n_days: int, n_pairs: int) -> set[tuple[int, int]]:
        days = self.days if self.days is not None else range(n_days)
        pairs = self.pairs if self.pairs is not None else range(1, n_pairs + 1)
        return {(d, p) for d in days for p in pairs}

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("teachers", "kind", "days", "pairs", "hard", "value", "room", "subject", "label")}

    @classmethod
    def from_dict(cls, data: dict) -> TeacherRule:
        return cls(**{k: data.get(k) for k in ("teachers", "kind", "days", "pairs", "hard", "value", "room", "subject", "label")
                      if data.get(k) is not None})

    def describe(self) -> str:
        days = ", ".join(DAYS[d].lower() for d in self.days) if self.days is not None else "усі дні"
        pairs = f"пари {', '.join(map(str, self.pairs))}" if self.pairs else "усі пари"
        strength = "" if self.hard else "за можливості "
        if self.kind == "unavailable":
            return f"{strength}не ставити: {days}, {pairs}"
        if self.kind == "max_days":
            return f"{strength}не більше {self.value} робочих днів на тиждень"
        if self.kind == "room":
            what = f"«{self.subject}» " if self.subject else ""
            return f"{strength}{what}лише в аудиторії {self.room}"
        return self.kind


@dataclass
class ProblemData:
    lessons: list[Lesson]
    groups: list[Group]
    rooms: list[Room]
    warnings: list[str] = field(default_factory=list)
    days: list[str] = field(default_factory=lambda: list(DAYS))
    # Вподобання й обмеження викладачів.
    rules: list[TeacherRule] = field(default_factory=list)

    def rules_for(self, teacher: str, kind: str) -> list[TeacherRule]:
        return [r for r in self.rules if r.kind == kind and teacher in r.teachers]

    @property
    def teachers(self) -> list[str]:
        return sorted({t for l in self.lessons for t in l.teachers}, key=uk_sort_key)


@dataclass
class Placement:
    """Заняття, поставлене в розклад."""

    lesson_id: int
    week: int  # 0 = чисельник, 1 = знаменник
    day: int  # індекс у ProblemData.days
    pair: int  # 1..8
    rooms: list[str] = field(default_factory=list)


@dataclass
class Issue:
    severity: str  # error | warning | info
    rule: str
    message: str


@dataclass
class Schedule:
    data: ProblemData
    placements: list[Placement]
    status: str
    objective: float | None = None
    solve_seconds: float = 0.0
    penalty_breakdown: dict[str, int] = field(default_factory=dict)
    semester: object = None  # semester.Semester — календар (суботи, тижні)
    issues: list[Issue] = field(default_factory=list)

    def lesson(self, lesson_id: int) -> Lesson:
        return self._lessons_by_id[lesson_id]

    def __post_init__(self):
        self._lessons_by_id = {l.id: l for l in self.data.lessons}

    def cell(self, group: str, week: int, day: int, pair: int) -> Placement | None:
        return self._index().get((group, week, day, pair))

    def _index(self):
        if not hasattr(self, "_idx"):
            self._idx = {
                (self.lesson(p.lesson_id).group, p.week, p.day, p.pair): p for p in self.placements
            }
        return self._idx
