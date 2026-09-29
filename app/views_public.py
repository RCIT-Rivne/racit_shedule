"""Відкритий розклад: групи й викладачі, день і тиждень, зміни. Вхід не потрібен."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import Blueprint, abort, make_response, render_template, request, url_for

from .db import Change, active_timetable
from .scheduler.models import DAYS, WEEKS
from .timetable import (
    BELLS,
    PAIRS,
    day_slots,
    for_group,
    for_teacher,
    groups_of,
    semester_of,
    teachers_of,
    week_dates,
)

bp = Blueprint("public", __name__)

WEEKDAY_NAMES = DAYS + ["Субота", "Неділя"]
MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня",
          "серпня", "вересня", "жовтня", "листопада", "грудня"]


def human_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]}"


def parse_day(value: str | None) -> date:
    """Дата з запиту; за замовчуванням — сьогодні, а після 18:00 і в неділю — наступний навчальний день."""
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    now = datetime.now()
    day = now.date()
    if now.hour >= 18:
        day += timedelta(days=1)
    if day.weekday() == 6:
        day += timedelta(days=1)
    return day


def day_info(tt, day: date) -> dict:
    """Підпис дня: тиждень (чисельник/знаменник), субота за чиїм розкладом, вихідний."""
    sem = semester_of(tt)
    resolved = sem.resolve(day)
    info = {"date": day, "weekday": WEEKDAY_NAMES[day.weekday()], "human": human_date(day), "off": resolved is None}
    if resolved:
        d, w = resolved
        info["week"] = WEEKS[w]
        if day.weekday() == 5:
            info["saturday_as"] = DAYS[d]
    elif not sem.start <= day <= sem.end:
        info["reason"] = "поза семестром"
    else:
        info["reason"] = "вихідний"
    return info


@bp.app_context_processor
def helpers():
    return {"bells": BELLS, "human_date": human_date, "weekday_names": WEEKDAY_NAMES,
            "change_kinds": Change.KINDS, "today": date.today()}


@bp.get("/")
def home():
    tt = active_timetable()
    if tt is None:
        return render_template("public/no_timetable.html")
    groups = groups_of(tt)
    courses: dict[int, list[str]] = {}
    for g in groups:
        courses.setdefault(g["course"], []).append(g["name"])
    last = request.cookies.get("last_view")
    return render_template("public/home.html", courses=courses, teachers=teachers_of(tt), last=last, today=parse_day(None))


def _render_owner(kind: str, name: str):
    tt = active_timetable()
    if tt is None:
        return render_template("public/no_timetable.html")
    if kind == "group" and name not in {g["name"] for g in groups_of(tt)}:
        abort(404)
    if kind == "teacher" and name not in teachers_of(tt):
        abort(404)
    day = parse_day(request.args.get("date"))
    view = "week" if request.args.get("view") == "week" else "day"
    pick = for_group if kind == "group" else for_teacher
    endpoint = "public.group" if kind == "group" else "public.teacher"

    if view == "day":
        days = [(day_info(tt, day), pick(day_slots(tt, day), name))]
        prev_day, next_day = day - timedelta(days=1), day + timedelta(days=1)
        if prev_day.weekday() == 6:
            prev_day -= timedelta(days=1)
        if next_day.weekday() == 6:
            next_day += timedelta(days=1)
    else:
        days = [(day_info(tt, d), pick(day_slots(tt, d), name)) for d in week_dates(day, tt.saturdays)]
        prev_day, next_day = day - timedelta(days=7), day + timedelta(days=7)

    response = make_response(render_template(
        "public/schedule.html",
        kind=kind,
        name=name,
        view=view,
        day=day,
        days=days,
        pairs=PAIRS,
        prev_url=url_for(endpoint, name=name, date=prev_day.isoformat(), view=view),
        next_url=url_for(endpoint, name=name, date=next_day.isoformat(), view=view),
        today_url=url_for(endpoint, name=name, view=view),
        switch_url=url_for(endpoint, name=name, date=day.isoformat(), view="day" if view == "week" else "week"),
    ))
    response.set_cookie("last_view", f"{kind}:{name}", max_age=60 * 60 * 24 * 365, samesite="Lax")
    return response


@bp.get("/g/<path:name>")
def group(name: str):
    return _render_owner("group", name)


@bp.get("/t/<path:name>")
def teacher(name: str):
    return _render_owner("teacher", name)


@bp.get("/changes")
def changes():
    """Усі зміни на дату — те, що зараз вручну розсилають увечері."""
    tt = active_timetable()
    if tt is None:
        return render_template("public/no_timetable.html")
    day = parse_day(request.args.get("date"))
    slots = [s for s in day_slots(tt, day) if s.is_changed]
    return render_template(
        "public/changes.html",
        info=day_info(tt, day),
        slots=slots,
        prev_url=url_for("public.changes", date=(day - timedelta(days=1)).isoformat()),
        next_url=url_for("public.changes", date=(day + timedelta(days=1)).isoformat()),
    )
