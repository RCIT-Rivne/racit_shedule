"""Адмін-панель навчальної частини: зміни розкладу, заявки, відсутності,
обмеження викладачів, практика, користувачі, постійні розклади."""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import delete, func, select

from .auth import admin_required, hash_password
from .db import (
    Absence,
    Change,
    Practice,
    Request,
    TeacherConstraint,
    TeacherPreference,
    Timetable,
    TimetableEntry,
    User,
    active_timetable,
    now,
    session,
)
from .scheduler.models import DAYS
from .timetable import (
    PAIRS,
    course_pairs,
    Slot,
    conflicts,
    day_slots,
    free_rooms,
    groups_of,
    substitute_candidates,
    teachers_of,
    week_dates,
)
from .views_public import day_info, parse_day
from .views_teacher import parse_date, parse_pairs

bp = Blueprint("admin", __name__, url_prefix="/admin")

HORIZON_DAYS = 14


def _need_decision(tt: Timetable, start: date, days: int = HORIZON_DAYS) -> list[Slot]:
    """Пари з відсутнім викладачем, для яких ще немає рішення (заміни чи скасування)."""
    out = []
    for i in range(days):
        out += [s for s in day_slots(tt, start + timedelta(days=i)) if s.missing]
    return out


@bp.get("")
@admin_required
def dashboard():
    tt = active_timetable()
    pending = session.scalar(select(func.count()).select_from(Request).where(Request.status == Request.PENDING))
    today = date.today()
    return render_template(
        "admin/dashboard.html",
        tt=tt,
        entries=session.scalar(select(func.count()).select_from(TimetableEntry).where(TimetableEntry.timetable_id == tt.id)) if tt else 0,
        pending=pending,
        need=_need_decision(tt, today) if tt else [],
        upcoming_changes=session.scalars(
            select(Change).where(Change.date >= today).order_by(Change.date, Change.pair).limit(12)
        ).all(),
        absences=session.scalars(
            select(Absence).where(Absence.date_to >= today).order_by(Absence.date_from)
        ).all(),
        horizon=HORIZON_DAYS,
    )


# --- Редактор дня --------------------------------------------------------------

@bp.get("/day")
@admin_required
def day():
    tt = active_timetable()
    if tt is None:
        flash("Спершу згенеруйте й опублікуйте постійний розклад")
        return redirect(url_for("gen.index"))
    day = parse_day(request.args.get("date"))
    groups = groups_of(tt)
    courses = sorted({g_["course"] for g_ in groups})
    course = request.args.get("course", type=int) or courses[0]
    names = [g_["name"] for g_ in groups if g_["course"] == course]
    slots = day_slots(tt, day)
    grid = {(s.group, s.pair): s for s in slots}
    # Лише пари, які в цього курсу бувають (зміна груп), плюс зайняті зараз.
    used = {p for p, group in course_pairs(tt) if group in names} | {s.pair for s in slots if s.group in names}
    return render_template(
        "admin/day.html",
        info=day_info(tt, day),
        day=day,
        week=[(d, day_info(tt, d)) for d in week_dates(day, tt.saturdays)],
        courses=courses,
        course=course,
        names=names,
        grid=grid,
        pairs=PAIRS if request.args.get("all") else ([p for p in PAIRS if p in used] or PAIRS),
        show_all=bool(request.args.get("all")),
        all_pairs=PAIRS,
        need=[s for s in slots if s.missing],
        changed=[s for s in slots if s.is_changed],
    )


def _slot(tt: Timetable, day: date, group: str, pair: int) -> tuple[Slot | None, list[Slot]]:
    slots = day_slots(tt, day)
    return next((s for s in slots if s.group == group and s.pair == pair), None), slots


def _clear_slot(day: date, group: str, pair: int) -> None:
    """Прибрати зміни пари (і другу половину перенесення)."""
    changes = session.scalars(select(Change).where(Change.date == day, Change.group == group, Change.pair == pair)).all()
    for c in changes:
        if c.move_id:
            session.execute(delete(Change).where(Change.move_id == c.move_id))
        else:
            session.delete(c)


@bp.route("/slot", methods=["GET", "POST"])
@admin_required
def slot():
    tt = active_timetable()
    day = parse_date(request.values.get("date"))
    group = request.values.get("group", "")
    pair = request.values.get("pair", type=int)
    if tt is None or day is None or pair not in PAIRS:
        flash("Некоректна пара")
        return redirect(url_for("admin.day"))
    current, slots = _slot(tt, day, group, pair)
    warnings: list[str] = []
    form = request.form

    if request.method == "POST":
        action = form.get("action")
        reason = form.get("reason") or None
        absence_id = form.get("absence_id", type=int)
        new_change: list[Change] = []

        if action == "reset":
            _clear_slot(day, group, pair)
            session.commit()
            flash("Пару повернуто до постійного розкладу")
            return redirect(url_for("admin.day", date=day.isoformat(), course=request.values.get("course")))

        base = current.original if current and current.original else None
        subject = (form.get("subject") or (current.subject if current else "")).strip()
        rooms = [r for r in form.getlist("rooms") if r]

        if action == "substitute" and current:
            teachers = [t for t in form.getlist("teachers") if t]
            if not teachers:
                warnings.append("Оберіть викладача на заміну")
            new_change.append(Change(kind=Change.SUBSTITUTE, subject=subject if subject != current.subject else None,
                                     teachers=teachers, rooms=rooms))
            check_teachers, check_rooms = teachers, rooms or current.rooms
        elif action == "cancel" and current:
            new_change.append(Change(kind=Change.CANCEL))
            check_teachers, check_rooms = [], []
        elif action == "room" and current:
            if not rooms:
                warnings.append("Оберіть аудиторію")
            new_change.append(Change(kind=Change.ROOM, rooms=rooms))
            check_teachers, check_rooms = [], rooms
        elif action == "add":
            teachers = [t for t in form.getlist("teachers") if t]
            if not subject or not teachers:
                warnings.append("Вкажіть дисципліну й викладача")
            new_change.append(Change(kind=Change.ADD, subject=subject, teachers=teachers, rooms=rooms))
            check_teachers, check_rooms = teachers, rooms
        elif action == "move" and current:
            target_day = parse_date(form.get("target_date"))
            target_pair = form.get("target_pair", type=int)
            if target_day is None or target_pair not in PAIRS:
                warnings.append("Вкажіть дату й пару, куди перенести")
                check_teachers, check_rooms = [], []
            else:
                target_slot, target_slots = _slot(tt, target_day, group, target_pair)
                if target_slot and target_slot.is_active:
                    warnings.append(f"У групи вже є пара {target_pair} {target_day.strftime('%d.%m')}: {target_slot.subject}")
                warnings += conflicts(target_slots, group, target_pair, current.teachers, rooms or current.rooms)
                move_id = uuid.uuid4().hex
                new_change.append(Change(kind=Change.CANCEL, move_id=move_id))
                new_change.append(Change(kind=Change.ADD, date=target_day, pair=target_pair, subject=current.subject,
                                         teachers=list(current.teachers), rooms=rooms or list(current.rooms), move_id=move_id))
                check_teachers, check_rooms = [], []
        else:
            flash("Невідома дія")
            return redirect(url_for("admin.slot", date=day.isoformat(), group=group, pair=pair))

        warnings += conflicts(slots, group, pair, check_teachers, check_rooms)
        blocking = [w for w in warnings if w.startswith(("Оберіть", "Вкажіть"))]
        if not blocking and (not warnings or form.get("force")):
            _clear_slot(day, group, pair)
            for c in new_change:
                c.date = c.date or day
                c.group = group
                c.pair = c.pair or pair
                c.reason = reason
                c.absence_id = absence_id
                c.created_by = g.user.id
                session.add(c)
            session.commit()
            flash("Зміну збережено" + (" (з попередженнями)" if warnings else ""))
            return redirect(url_for("admin.day", date=day.isoformat(), course=request.values.get("course")))

    absence = None
    if current and current.missing:
        absence = session.scalar(select(Absence).where(
            Absence.teacher == current.missing[0], Absence.date_from <= day, Absence.date_to >= day))
    return render_template(
        "admin/slot.html",
        info=day_info(tt, day),
        day=day,
        group=group,
        pair=pair,
        current=current,
        candidates=substitute_candidates(tt, slots, current) if current else [],
        rooms=free_rooms(tt, slots, pair),
        teachers=teachers_of(tt),
        pairs=PAIRS,
        warnings=warnings,
        form=form,
        absence=absence,
        course=request.values.get("course"),
    )


# --- Заявки викладачів -----------------------------------------------------------

@bp.get("/requests")
@admin_required
def requests_list():
    rows = session.scalars(select(Request).order_by(Request.status != Request.PENDING, Request.created_at.desc())).all()
    return render_template("admin/requests.html", rows=rows, kinds=Request.KINDS, statuses=Request.STATUSES, weekdays=DAYS)


@bp.post("/requests/<int:request_id>/<decision>")
@admin_required
def decide(request_id: int, decision: str):
    req = session.get(Request, request_id)
    if req is None or req.status != Request.PENDING or decision not in ("approve", "reject"):
        flash("Заявку вже розглянуто")
        return redirect(url_for("admin.requests_list"))
    req.admin_comment = request.form.get("admin_comment") or None
    req.decided_by, req.decided_at = g.user.id, now()
    if decision == "reject":
        req.status = Request.REJECTED
        session.commit()
        flash("Заявку відхилено")
        return redirect(url_for("admin.requests_list"))

    req.status = Request.APPROVED
    if req.kind == Request.PERMANENT:
        session.add(TeacherConstraint(teacher=req.teacher, weekday=req.weekday, pairs=req.pairs,
                                      note=req.comment, request_id=req.id))
        session.commit()
        flash(f"Додано постійне обмеження: {req.teacher}, {DAYS[req.weekday].lower()}. "
              "Воно врахується при наступній генерації; до того — вносьте заміни в редакторі дня.")
        return redirect(url_for("admin.constraints"))
    absence = Absence(teacher=req.teacher, date_from=req.date_from, date_to=req.date_to, pairs=req.pairs,
                      reason="sick" if req.kind == Request.SICK else "personal", note=req.comment,
                      request_id=req.id, created_by=g.user.id)
    session.add(absence)
    session.commit()
    flash(f"Відсутність {req.teacher} збережено. Пари, що потребують заміни, — нижче.")
    return redirect(url_for("admin.absences"))


# --- Відсутності ------------------------------------------------------------------

@bp.route("/absences", methods=["GET", "POST"])
@admin_required
def absences():
    tt = active_timetable()
    if request.method == "POST":
        teacher = request.form.get("teacher", "")
        start = parse_date(request.form.get("date_from"))
        end = parse_date(request.form.get("date_to")) or start
        if not teacher or start is None or end < start:
            flash("Вкажіть викладача й коректні дати")
        else:
            session.add(Absence(teacher=teacher, date_from=start, date_to=end,
                                pairs=parse_pairs(request.form.getlist("pairs")),
                                reason=request.form.get("reason", "other"), note=request.form.get("note") or None,
                                created_by=g.user.id))
            session.commit()
            flash("Відсутність збережено")
        return redirect(url_for("admin.absences"))
    rows = session.scalars(select(Absence).where(Absence.date_to >= date.today() - timedelta(days=30))
                           .order_by(Absence.date_from.desc())).all()
    affected = {}
    if tt:
        for a in rows:
            if a.date_to >= date.today():
                start = max(a.date_from, date.today())
                span = min((a.date_to - start).days + 1, 31)
                affected[a.id] = [s for s in _need_decision(tt, start, span) if a.teacher in s.missing]
    return render_template("admin/absences.html", rows=rows, affected=affected, reasons=Absence.REASONS,
                           teachers=teachers_of(tt) if tt else [], pairs=PAIRS, today=date.today().isoformat())


@bp.post("/absences/<int:absence_id>/delete")
@admin_required
def delete_absence(absence_id: int):
    a = session.get(Absence, absence_id)
    if a:
        session.delete(a)
        session.commit()
        flash("Відсутність видалено")
    return redirect(url_for("admin.absences"))


# --- Постійні обмеження ----------------------------------------------------------

@bp.route("/constraints", methods=["GET", "POST"])
@admin_required
def constraints():
    tt = active_timetable()
    if request.method == "POST":
        teacher = request.form.get("teacher", "")
        weekday = request.form.get("weekday", type=int)
        if not teacher or weekday not in range(len(DAYS)):
            flash("Оберіть викладача й день тижня")
        else:
            session.add(TeacherConstraint(teacher=teacher, weekday=weekday,
                                          pairs=parse_pairs(request.form.getlist("pairs")),
                                          note=request.form.get("note") or None))
            session.commit()
            flash("Обмеження додано — воно врахується при наступній генерації")
        return redirect(url_for("admin.constraints"))
    rows = session.scalars(select(TeacherConstraint).order_by(TeacherConstraint.teacher, TeacherConstraint.weekday)).all()
    # Скільки пар у поточному розкладі суперечать обмеженню.
    clashes = {}
    if tt:
        for c in rows:
            entries = session.scalars(select(TimetableEntry).where(
                TimetableEntry.timetable_id == tt.id, TimetableEntry.day == c.weekday)).all()
            clashes[c.id] = len({(e.week, e.pair) for e in entries
                                 if c.teacher in e.teachers and (not c.pairs or e.pair in c.pairs)})
    return render_template("admin/constraints.html", rows=rows, clashes=clashes, weekdays=DAYS, pairs=PAIRS,
                           teachers=teachers_of(tt) if tt else [])


@bp.post("/constraints/<int:constraint_id>/delete")
@admin_required
def delete_constraint(constraint_id: int):
    c = session.get(TeacherConstraint, constraint_id)
    if c:
        session.delete(c)
        session.commit()
        flash("Обмеження видалено")
    return redirect(url_for("admin.constraints"))


def solver_rules() -> list[dict]:
    """Правила для генератора: активні вподобання + постійні обмеження зі схвалених заявок."""
    rules = [p.to_rule().to_dict() for p in
             session.scalars(select(TeacherPreference).where(TeacherPreference.active.is_(True)))]
    for c in session.scalars(select(TeacherConstraint)):
        rules.append({"teachers": [c.teacher], "kind": "unavailable", "days": [c.weekday], "pairs": c.pairs,
                      "hard": True, "label": f"Обмеження: {c.note or 'із заявки'}"})
    return rules


# --- Вподобання викладачів -------------------------------------------------------

def _int_list(values: list[str]) -> list[int] | None:
    out = sorted({int(v) for v in values if v.isdigit()})
    return out or None


@bp.route("/preferences", methods=["GET", "POST"])
@admin_required
def preferences():
    tt = active_timetable()
    if request.method == "POST":
        teachers = [t for t in request.form.getlist("teachers") if t]
        kind = request.form.get("kind")
        pref = TeacherPreference(
            label=request.form.get("label", "").strip(), teachers=teachers, kind=kind,
            days=_int_list(request.form.getlist("days")), pairs=parse_pairs(request.form.getlist("pairs")),
            hard=request.form.get("hard") == "1", value=request.form.get("value", type=int),
            room=request.form.get("room") or None,
        )
        error = None
        if not teachers or kind not in TeacherPreference.KINDS:
            error = "Оберіть викладачів і тип правила"
        elif kind == "max_days" and not (pref.value and 1 <= pref.value <= 5):
            error = "Вкажіть кількість днів від 1 до 5"
        elif kind == "room" and not pref.room:
            error = "Оберіть аудиторію"
        elif kind == "unavailable" and not pref.days and not pref.pairs:
            error = "Оберіть дні та/або пари, коли не ставити"
        if error:
            flash(error)
        else:
            session.add(pref)
            session.commit()
            flash("Правило додано — воно врахується при наступній генерації")
        return redirect(url_for("admin.preferences"))
    rows = session.scalars(
        select(TeacherPreference).order_by(TeacherPreference.active.desc(), TeacherPreference.id)).all()
    from .timetable import rooms_of

    return render_template(
        "admin/preferences.html", rows=rows, kinds=TeacherPreference.KINDS, weekdays=DAYS, pairs=PAIRS,
        teachers=teachers_of(tt) if tt else [], rooms=rooms_of(tt) if tt else [],
        constraints=session.scalars(select(TeacherConstraint)).all(),
    )


@bp.post("/preferences/<int:pref_id>/<action>")
@admin_required
def preference_action(pref_id: int, action: str):
    pref = session.get(TeacherPreference, pref_id)
    if pref and action == "toggle":
        pref.active = not pref.active
    elif pref and action == "delete":
        session.delete(pref)
    session.commit()
    return redirect(url_for("admin.preferences"))


@bp.post("/preferences/import")
@admin_required
def import_preferences():
    added = import_rules_file(current_app.config["RULES_FILE"])
    flash(f"Імпортовано правил: {added}")
    return redirect(url_for("admin.preferences"))


def import_rules_file(path) -> int:
    """Імпорт правил з JSON; правила з уже наявною назвою пропускаються."""
    import json

    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    existing = {p.label for p in session.scalars(select(TeacherPreference))}
    added = 0
    for item in items:
        if item.get("label") in existing:
            continue
        session.add(TeacherPreference(label=item.get("label", ""), teachers=item["teachers"], kind=item["kind"],
                                      days=item.get("days"), pairs=item.get("pairs"), hard=item.get("hard", True),
                                      value=item.get("value"), room=item.get("room")))
        added += 1
    session.commit()
    return added


# --- Практика груп -----------------------------------------------------------------

@bp.route("/practice", methods=["GET", "POST"])
@admin_required
def practice():
    tt = active_timetable()
    if request.method == "POST":
        group = request.form.get("group", "")
        start = parse_date(request.form.get("date_from"))
        end = parse_date(request.form.get("date_to"))
        if not group or start is None or end is None or end < start:
            flash("Вкажіть групу й коректні дати")
        else:
            session.add(Practice(group=group, date_from=start, date_to=end, note=request.form.get("note") or None))
            session.commit()
            flash("Практику додано — пари групи в ці дні не показуються")
        return redirect(url_for("admin.practice"))
    rows = session.scalars(select(Practice).order_by(Practice.date_from.desc())).all()
    return render_template("admin/practice.html", rows=rows, groups=groups_of(tt) if tt else [])


@bp.post("/practice/<int:practice_id>/delete")
@admin_required
def delete_practice(practice_id: int):
    p = session.get(Practice, practice_id)
    if p:
        session.delete(p)
        session.commit()
    return redirect(url_for("admin.practice"))


# --- Користувачі -----------------------------------------------------------------

@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    tt = active_timetable()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role") if request.form.get("role") in (User.ADMIN, User.TEACHER) else User.TEACHER
        teacher = request.form.get("teacher_name") or None
        if not email or len(password) < 8:
            flash("Потрібні email і пароль щонайменше з 8 символів")
        elif session.scalar(select(User).where(func.lower(User.email) == email)):
            flash("Користувач з таким email уже є")
        else:
            session.add(User(email=email, full_name=teacher or request.form.get("full_name") or email,
                             password_hash=hash_password(password), role=role, teacher_name=teacher))
            session.commit()
            flash("Користувача створено")
        return redirect(url_for("admin.users"))
    rows = session.scalars(select(User).order_by(User.role, User.full_name)).all()
    return render_template("admin/users.html", rows=rows, teachers=teachers_of(tt) if tt else [])


@bp.post("/users/<int:user_id>/delete")
@admin_required
def delete_user(user_id: int):
    user = session.get(User, user_id)
    if user and user.id != g.user.id:
        session.execute(delete(Request).where(Request.user_id == user.id))
        session.delete(user)
        session.commit()
        flash("Користувача видалено")
    return redirect(url_for("admin.users"))


# --- Постійні розклади -----------------------------------------------------------

@bp.get("/timetables")
@admin_required
def timetables():
    rows = session.scalars(select(Timetable).order_by(Timetable.created_at.desc())).all()
    counts = dict(session.execute(select(TimetableEntry.timetable_id, func.count()).group_by(TimetableEntry.timetable_id)).all())
    return render_template("admin/timetables.html", rows=rows, counts=counts)


@bp.post("/timetables/<int:tt_id>/activate")
@admin_required
def activate(tt_id: int):
    tt = session.get(Timetable, tt_id)
    if tt:
        for other in session.scalars(select(Timetable).where(Timetable.is_active.is_(True))):
            other.is_active = False
        tt.is_active = True
        session.commit()
        flash(f"Активний розклад: {tt.name}")
    return redirect(url_for("admin.timetables"))
