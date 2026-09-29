"""Кабінет викладача: свій розклад і заявки (лікарняний, не можу провести пари, постійне обмеження)."""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from sqlalchemy import select

from .auth import login_required
from .db import Absence, Request, active_timetable, session
from .scheduler.models import DAYS
from .timetable import PAIRS, day_slots, for_teacher
from .views_public import day_info, parse_day

bp = Blueprint("teacher", __name__, url_prefix="/me")


def parse_pairs(values: list[str]) -> list[int] | None:
    pairs = sorted({int(v) for v in values if v.isdigit() and int(v) in PAIRS})
    return pairs or None  # None — увесь день


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@bp.get("")
@login_required
def home():
    if g.user.is_admin and not g.user.teacher_name:
        return redirect(url_for("admin.dashboard"))
    tt = active_timetable()
    name = g.user.teacher_name
    days = []
    if tt and name:
        day = parse_day(None)
        for d in (day, day + timedelta(days=1 if day.weekday() < 5 else 2)):
            days.append((day_info(tt, d), for_teacher(day_slots(tt, d), name)))
    requests = session.scalars(
        select(Request).where(Request.user_id == g.user.id).order_by(Request.created_at.desc())
    ).all()
    absences = session.scalars(
        select(Absence).where(Absence.teacher == name, Absence.date_to >= date.today()).order_by(Absence.date_from)
    ).all() if name else []
    return render_template(
        "teacher/home.html",
        days=days,
        requests=requests,
        absences=absences,
        kinds=Request.KINDS,
        statuses=Request.STATUSES,
        reasons=Absence.REASONS,
        weekdays=DAYS,
        pairs=PAIRS,
        tomorrow=(date.today() + timedelta(days=1)).isoformat(),
        has_timetable=tt is not None,
    )


@bp.post("/requests")
@login_required
def create_request():
    if not g.user.teacher_name:
        flash("Акаунт не прив'язаний до викладача в розкладі — зверніться до навчальної частини")
        return redirect(url_for("teacher.home"))
    kind = request.form.get("kind")
    if kind not in Request.KINDS:
        flash("Оберіть тип заявки")
        return redirect(url_for("teacher.home"))
    req = Request(user_id=g.user.id, teacher=g.user.teacher_name, kind=kind, comment=request.form.get("comment") or None)
    if kind == Request.PERMANENT:
        weekday = request.form.get("weekday", type=int)
        if weekday not in range(len(DAYS)):
            flash("Оберіть день тижня")
            return redirect(url_for("teacher.home"))
        req.weekday = weekday
        req.pairs = parse_pairs(request.form.getlist("pairs"))
    else:
        start = parse_date(request.form.get("date_from"))
        end = parse_date(request.form.get("date_to")) or start
        if start is None or end < start:
            flash("Вкажіть коректні дати")
            return redirect(url_for("teacher.home"))
        req.date_from, req.date_to = start, end
        if kind == Request.DAY_OFF:
            req.pairs = parse_pairs(request.form.getlist("pairs"))
    session.add(req)
    session.commit()
    flash("Заявку надіслано в навчальну частину")
    return redirect(url_for("teacher.home"))


@bp.post("/requests/<int:request_id>/withdraw")
@login_required
def withdraw(request_id: int):
    req = session.get(Request, request_id)
    if req and req.user_id == g.user.id and req.status == Request.PENDING:
        session.delete(req)
        session.commit()
        flash("Заявку відкликано")
    return redirect(url_for("teacher.home"))
