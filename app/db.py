"""База даних застосунку (SQLite через SQLAlchemy).

Постійний розклад (Timetable + TimetableEntry) — опублікований результат
генерації. Усе, що відбувається з розкладом після публікації, зберігається
окремо й прив'язане до дат: зміни (Change), відсутності викладачів (Absence),
практика груп (Practice). Постійні обмеження викладачів (TeacherConstraint)
йдуть у генератор при наступній генерації.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, scoped_session, sessionmaker

session = scoped_session(sessionmaker(expire_on_commit=False))


class Base(DeclarativeBase):
    pass


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


class User(Base):
    __tablename__ = "users"
    ADMIN = "admin"
    TEACHER = "teacher"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20))
    # Ім'я викладача так, як воно записане в розкладі («Бубнов О. В.»).
    teacher_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    @property
    def is_admin(self) -> bool:
        return self.role == self.ADMIN


class Timetable(Base):
    """Постійний розклад на семестр (чисельник + знаменник, пн–пт)."""

    __tablename__ = "timetables"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source_job: Mapped[str | None] = mapped_column(String(40))
    semester_start: Mapped[date] = mapped_column(Date)
    semester_end: Mapped[date] = mapped_column(Date)
    first_week: Mapped[int] = mapped_column(Integer, default=0)
    saturdays: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)

    entries: Mapped[list[TimetableEntry]] = relationship(
        back_populates="timetable", cascade="all, delete-orphan"
    )


class TimetableEntry(Base):
    __tablename__ = "timetable_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    timetable_id: Mapped[int] = mapped_column(ForeignKey("timetables.id"), index=True)
    group: Mapped[str] = mapped_column(String(40), index=True)
    course: Mapped[int] = mapped_column(Integer, default=0)
    subject: Mapped[str] = mapped_column(String(300))
    teachers: Mapped[list] = mapped_column(JSON, default=list)
    rooms: Mapped[list] = mapped_column(JSON, default=list)
    week: Mapped[int] = mapped_column(Integer)  # 0 — чисельник, 1 — знаменник
    day: Mapped[int] = mapped_column(Integer)  # 0..4 — пн..пт
    pair: Mapped[int] = mapped_column(Integer)

    timetable: Mapped[Timetable] = relationship(back_populates="entries")


class Change(Base):
    """Зміна на конкретну дату для пари групи: заміна, скасування, аудиторія,
    додаткова пара. Перенесення = скасування + додаткова пара з одним move_id."""

    __tablename__ = "changes"
    SUBSTITUTE = "substitute"
    CANCEL = "cancel"
    ROOM = "room"
    ADD = "add"
    KINDS = {
        SUBSTITUTE: "Заміна викладача",
        CANCEL: "Пару скасовано",
        ROOM: "Інша аудиторія",
        ADD: "Додаткова пара",
    }

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    group: Mapped[str] = mapped_column(String(40), index=True)
    pair: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str | None] = mapped_column(String(300))
    teachers: Mapped[list] = mapped_column(JSON, default=list)
    rooms: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str | None] = mapped_column(Text)
    move_id: Mapped[str | None] = mapped_column(String(40))
    absence_id: Mapped[int | None] = mapped_column(ForeignKey("absences.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Absence(Base):
    """Відсутність викладача: лікарняний, відрядження тощо. pairs=None — увесь день."""

    __tablename__ = "absences"
    REASONS = {
        "sick": "Лікарняний",
        "trip": "Відрядження",
        "personal": "Особисті обставини",
        "other": "Інше",
    }

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher: Mapped[str] = mapped_column(String(200), index=True)
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    pairs: Mapped[list | None] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(20), default="other")
    note: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("requests.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def covers(self, day: date, pair: int) -> bool:
        return self.date_from <= day <= self.date_to and (not self.pairs or pair in self.pairs)


class TeacherConstraint(Base):
    """Постійне обмеження: викладач не може в цей день тижня (усі або окремі пари).
    Враховується генератором як жорстке правило."""

    __tablename__ = "teacher_constraints"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher: Mapped[str] = mapped_column(String(200), index=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 0..4
    pairs: Mapped[list | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("requests.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class TeacherPreference(Base):
    """Вподобання викладача або групи викладачів (див. scheduler.models.TeacherRule)."""

    __tablename__ = "teacher_preferences"
    KINDS = {
        "unavailable": "Не ставити в дні / пари",
        "max_days": "Не більше N робочих днів на тиждень",
        "room": "Лише певна аудиторія",
    }

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(300), default="")
    teachers: Mapped[list] = mapped_column(JSON, default=list)
    kind: Mapped[str] = mapped_column(String(20))
    days: Mapped[list | None] = mapped_column(JSON)
    pairs: Mapped[list | None] = mapped_column(JSON)
    hard: Mapped[bool] = mapped_column(Boolean, default=True)
    value: Mapped[int | None] = mapped_column(Integer)
    room: Mapped[str | None] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    def to_rule(self):
        from .scheduler.models import TeacherRule

        return TeacherRule(teachers=list(self.teachers), kind=self.kind, days=self.days, pairs=self.pairs,
                           hard=self.hard, value=self.value, room=self.room, label=self.label)


class Request(Base):
    """Заявка викладача. Після схвалення стає відсутністю або постійним обмеженням."""

    __tablename__ = "requests"
    SICK = "sick"
    DAY_OFF = "day_off"
    PERMANENT = "permanent"
    KINDS = {
        SICK: "Лікарняний",
        DAY_OFF: "Не можу провести пари",
        PERMANENT: "Постійно не можу в день тижня",
    }
    PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
    STATUSES = {PENDING: "На розгляді", APPROVED: "Схвалено", REJECTED: "Відхилено"}

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    teacher: Mapped[str] = mapped_column(String(200), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    date_from: Mapped[date | None] = mapped_column(Date)
    date_to: Mapped[date | None] = mapped_column(Date)
    weekday: Mapped[int | None] = mapped_column(Integer)
    pairs: Mapped[list | None] = mapped_column(JSON)
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=PENDING)
    admin_comment: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class Practice(Base):
    """Група на практиці: пар у розкладі в ці дати немає."""

    __tablename__ = "practices"

    id: Mapped[int] = mapped_column(primary_key=True)
    group: Mapped[str] = mapped_column(String(40), index=True)
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


def init_db(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{data_dir / 'app.db'}", connect_args={"check_same_thread": False})
    session.configure(bind=engine)
    Base.metadata.create_all(engine)


def active_timetable() -> Timetable | None:
    return session.scalar(select(Timetable).where(Timetable.is_active.is_(True)))
