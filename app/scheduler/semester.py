"""Календар семестру: чисельник/знаменник і навчальні суботи.

Тижні чергуються: тиждень, у який починається семестр, — `first_week`
(за замовчуванням чисельник), далі по черзі.
Субота повторює будній день за ротацією: перша субота семестру — за розкладом
понеділка, наступна — вівторка, … п'ятниці, далі знову понеділка. Суботні пари
беруться з того ж тижня (чисельник/знаменник), до якого належить субота.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .models import DAYS, SATURDAY_NAME, WEEKS

SATURDAY = 5
SUNDAY = 6


@dataclass
class Semester:
    start: date
    end: date
    first_week: int = 0  # 0 — чисельник, 1 — знаменник
    saturdays: bool = True

    @classmethod
    def default(cls, today: date | None = None) -> Semester:
        """Поточний семестр: вересень–грудень або січень–червень."""
        today = today or date.today()
        if today.month >= 8:
            return cls(date(today.year, 9, 1), date(today.year, 12, 31))
        return cls(date(today.year, 1, 1), date(today.year, 6, 30))

    def week_of(self, day: date) -> int:
        monday = self.start - timedelta(days=self.start.weekday())
        return ((day - monday).days // 7 + self.first_week) % 2

    def saturdays_list(self) -> list[dict]:
        """[{date, day, week}] — кожна субота семестру і день, за яким вона навчається."""
        if not self.saturdays:
            return []
        first = self.start + timedelta(days=(SATURDAY - self.start.weekday()) % 7)
        out = []
        current, i = first, 0
        while current <= self.end:
            out.append({"date": current, "day": i % len(DAYS), "week": self.week_of(current)})
            current += timedelta(days=7)
            i += 1
        return out

    def resolve(self, day: date) -> tuple[int, int] | None:
        """Дата -> (індекс буднього дня розкладу, тиждень) або None (вихідний / поза семестром)."""
        if not self.start <= day <= self.end:
            return None
        weekday = day.weekday()
        if weekday < SATURDAY:
            return weekday, self.week_of(day)
        if weekday == SATURDAY:
            for s in self.saturdays_list():
                if s["date"] == day:
                    return s["day"], s["week"]
        return None

    def describe(self, day: date) -> str:
        resolved = self.resolve(day)
        if resolved is None:
            return "вихідний"
        d, w = resolved
        if day.weekday() == SATURDAY:
            return f"{SATURDAY_NAME} за розкладом: {DAYS[d].lower()}, {WEEKS[w].lower()}"
        return f"{DAYS[d]}, {WEEKS[w].lower()}"

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "first_week": self.first_week,
            "saturdays": self.saturdays,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> Semester:
        if not data:
            return cls.default()
        return cls(
            date.fromisoformat(data["start"]),
            date.fromisoformat(data["end"]),
            int(data.get("first_week", 0)),
            bool(data.get("saturdays", True)),
        )
