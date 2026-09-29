"""Веб-частина: публічний розклад, кабінет викладача, заявки, заміни в адмінці."""
from datetime import date, timedelta

import pytest

from app.scheduler import Semester, SolverConfig
from app.scheduler.rooms import assign_rooms
from app.scheduler.solver import solve
from tests.test_scheduler import small_problem

SEMESTER = Semester(date(2026, 9, 1), date(2026, 12, 31))
MONDAY = date(2026, 10, 5)


@pytest.fixture(scope="module")
def schedule():
    s = solve(small_problem(), SolverConfig(time_limit=10))
    assign_rooms(s)
    s.semester = SEMESTER
    return s


@pytest.fixture()
def app(tmp_path, monkeypatch, schedule):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_EMAIL", "admin@test.ua")
    monkeypatch.setenv("ADMIN_PASSWORD", "adminpass1")
    from app import create_app, db
    from app.auth import hash_password
    from app.timetable import publish

    app = create_app()
    with app.app_context():
        publish(schedule, "Тест", None)
        db.session.add(db.User(email="t@test.ua", full_name="Іванов І. І.", password_hash=hash_password("teachpass1"),
                               role="teacher", teacher_name="Іванов І. І."))
        db.session.commit()
    yield app
    db.session.remove()


def login(client, email, password):
    with client.session_transaction() as s:
        s["csrf"] = "t"
    client.post("/login", data={"email": email, "password": password, "csrf": "t"})
    with client.session_transaction() as s:
        s["csrf"] = "t"


def test_public_pages(app):
    c = app.test_client()
    assert c.get("/").status_code == 200
    r = c.get(f"/g/A-1/1?date={MONDAY}")
    assert r.status_code == 200 and "A-1/1" in r.text
    assert c.get(f"/g/A-1/1?date={MONDAY}&view=week").status_code == 200
    assert c.get("/t/Іванов І. І.").status_code == 200
    assert c.get("/g/Немає").status_code == 404
    assert c.get("/admin").status_code == 302  # потрібен вхід


def test_csrf_required(app):
    c = app.test_client()
    assert c.post("/login", data={"email": "admin@test.ua", "password": "adminpass1"}).status_code == 400


def test_teacher_cannot_open_admin(app):
    c = app.test_client()
    login(c, "t@test.ua", "teachpass1")
    assert c.get("/me").status_code == 200
    assert c.get("/admin").status_code == 403


def test_sick_request_to_substitution(app):
    """Лікарняний → схвалення → пари без викладача → заміна → студент бачить заміну."""
    from app import db
    from app.timetable import day_slots

    teacher = app.test_client()
    login(teacher, "t@test.ua", "teachpass1")
    r = teacher.post("/me/requests", data={"kind": "sick", "date_from": MONDAY.isoformat(),
                                          "date_to": (MONDAY + timedelta(days=4)).isoformat(), "csrf": "t"})
    assert r.status_code == 302

    admin = app.test_client()
    login(admin, "admin@test.ua", "adminpass1")
    with app.app_context():
        req = db.session.query(db.Request).one()
    assert admin.post(f"/admin/requests/{req.id}/approve", data={"csrf": "t"}).status_code == 302

    with app.app_context():
        tt = db.active_timetable()
        missing = [s for d in range(5) for s in day_slots(tt, MONDAY + timedelta(days=d)) if s.missing]
    assert missing and all(s.missing == ["Іванов І. І."] for s in missing)

    slot = missing[0]
    page = admin.get(f"/admin/slot?date={slot.date}&group={slot.group}&pair={slot.pair}")
    assert page.status_code == 200 and "Вільні викладачі" in page.text
    r = admin.post("/admin/slot", data={"date": slot.date.isoformat(), "group": slot.group, "pair": slot.pair,
                                        "action": "substitute", "teachers": ["Петров П. П."], "force": "1", "csrf": "t"})
    assert r.status_code == 302

    public = app.test_client().get(f"/g/{slot.group}?date={slot.date}")
    assert "Заміна" in public.text and "Петров П. П." in public.text
    assert slot.group in app.test_client().get(f"/changes?date={slot.date}").text


def test_move_and_reset(app):
    from app import db
    from app.timetable import day_slots

    admin = app.test_client()
    login(admin, "admin@test.ua", "adminpass1")
    with app.app_context():
        slot = day_slots(db.active_timetable(), MONDAY)[0]
    target = MONDAY + timedelta(days=5)  # субота
    r = admin.post("/admin/slot", data={"date": MONDAY.isoformat(), "group": slot.group, "pair": slot.pair,
                                        "action": "move", "target_date": target.isoformat(), "target_pair": 7,
                                        "force": "1", "csrf": "t"})
    assert r.status_code == 302
    assert "Перенесено" in app.test_client().get(f"/g/{slot.group}?date={target}").text
    assert "Скасовано" in app.test_client().get(f"/g/{slot.group}?date={MONDAY}").text
    admin.post("/admin/slot", data={"date": MONDAY.isoformat(), "group": slot.group, "pair": slot.pair,
                                    "action": "reset", "csrf": "t"})
    assert "Перенесено" not in app.test_client().get(f"/g/{slot.group}?date={target}").text


def test_constraint_reaches_solver(app):
    from app import db
    from app.scheduler.models import TeacherRule
    from app.views_admin import solver_rules

    with app.app_context():
        db.session.add(db.TeacherConstraint(teacher="Іванов І. І.", weekday=4, pairs=None))
        db.session.commit()
        rules = [TeacherRule.from_dict(r) for r in solver_rules()]
    assert rules[0].teachers == ["Іванов І. І."] and rules[0].days == [4] and rules[0].hard

    data = small_problem()
    data.rules = rules
    s = solve(data, SolverConfig(time_limit=10))
    ivanov = {l.id for l in data.lessons if "Іванов І. І." in l.teachers}
    assert s.placements and not [p for p in s.placements if p.lesson_id in ivanov and p.day == 4]


def test_practice_hides_lessons(app):
    admin = app.test_client()
    login(admin, "admin@test.ua", "adminpass1")
    admin.post("/admin/practice", data={"group": "A-1/1", "date_from": MONDAY.isoformat(),
                                        "date_to": MONDAY.isoformat(), "csrf": "t"})
    assert "Практика" in app.test_client().get(f"/g/A-1/1?date={MONDAY}").text
