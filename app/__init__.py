"""Розклад РФКІТ: генерація, постійний розклад, зміни, кабінети студента й викладача."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

import click
from flask import Flask, g, render_template
from sqlalchemy import func, select

from . import db
from .auth import check_csrf, csrf_token, ensure_admin, hash_password, load_user
from .jobs import JobStore

BASE_DIR = Path(__file__).resolve().parent.parent


def _secret_key(data_dir: Path) -> str:
    """SECRET_KEY зі середовища або згенерований і збережений у DATA_DIR (щоб сесії жили після перезапуску)."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = data_dir / "secret_key"
    if not path.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def create_app() -> Flask:
    app = Flask(__name__)
    data_dir = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
    app.config.update(
        SECRET_KEY=_secret_key(data_dir),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        DATA_DIR=data_dir,
        SAMPLE_INPUT=Path(os.environ.get("SAMPLE_INPUT", BASE_DIR / "info" / "Розклад на 2026 р (інформація).xlsx")),
        DEFAULT_TIME_LIMIT=float(os.environ.get("SOLVER_TIME_LIMIT", 120)),
        RULES_FILE=Path(os.environ.get("RULES_FILE", BASE_DIR / "config" / "teacher_rules.json")),
        # До грудня 2026 розклад береться з шаблону, генератор схований.
        GENERATOR_ENABLED=os.environ.get("GENERATOR_ENABLED", "0") == "1",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    db.init_db(data_dir)
    from .timetable import clear_cache

    clear_cache()
    ensure_admin(os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD"))
    db.session.remove()
    app.extensions["jobs"] = JobStore(data_dir, workers=int(os.environ.get("SOLVER_WORKERS", 0)))

    from .auth import bp as auth_bp
    from .views_admin import bp as admin_bp
    from .views_generate import bp as gen_bp
    from .views_public import bp as public_bp
    from .views_teacher import bp as teacher_bp

    for bp in (public_bp, auth_bp, teacher_bp, admin_bp, gen_bp):
        app.register_blueprint(bp)

    app.before_request(load_user)
    app.before_request(check_csrf)

    @app.teardown_appcontext
    def remove_session(_exc):
        db.session.remove()

    @app.context_processor
    def inject():
        user = getattr(g, "user", None)
        pending = 0
        if user is not None and user.is_admin:
            pending = db.session.scalar(
                select(func.count()).select_from(db.Request).where(db.Request.status == db.Request.PENDING)
            )
        return {"csrf_token": csrf_token, "current_user": user, "pending_requests": pending,
                "generator_enabled": app.config["GENERATOR_ENABLED"]}

    def error_page(code: int, title: str):
        return lambda e: (render_template("error.html", code=code, title=title, error=e), code)

    app.register_error_handler(400, error_page(400, "Некоректний запит"))
    app.register_error_handler(403, error_page(403, "Немає доступу"))
    app.register_error_handler(404, error_page(404, "Сторінку не знайдено"))

    @app.cli.command("create-admin")
    @click.argument("email")
    @click.password_option()
    def create_admin(email, password):
        """Створити адміністратора."""
        db.session.add(db.User(email=email.lower(), full_name="Адміністратор",
                               password_hash=hash_password(password), role=db.User.ADMIN))
        db.session.commit()
        click.echo(f"Адміністратора {email} створено")

    @app.cli.command("publish-job")
    @click.argument("job_id")
    @click.option("--name", default=None, help="назва постійного розкладу")
    def publish_job(job_id, name):
        """Опублікувати результат генерації як постійний розклад (без повторної генерації)."""
        from .timetable import publish

        schedule = app.extensions["jobs"].schedule(job_id)
        if schedule is None:
            raise click.ClickException(f"Немає збереженого розкладу для задачі {job_id}")
        tt = publish(schedule, name or f"Розклад (генерація {job_id})", job_id)
        click.echo(f"Опубліковано «{tt.name}»: {len(tt.entries)} пар")

    @app.cli.command("import-template")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option("--daily", "daily_dir", type=click.Path(exists=True, file_okay=False, path_type=Path),
                  help="тека з щоденними файлами для аудиторій (за замовчуванням — тека шаблону)")
    @click.option("--start", type=click.DateTime(["%Y-%m-%d"]), default="2026-09-01", show_default=True)
    @click.option("--end", type=click.DateTime(["%Y-%m-%d"]), default="2026-12-31", show_default=True)
    @click.option("--first-week", type=click.Choice(["0", "1"]), default="0", show_default=True,
                  help="тиждень початку семестру: 0 — чисельник, 1 — знаменник")
    @click.option("--name", default=None, help="назва постійного розкладу")
    @click.option("--dry-run", is_flag=True, help="лише звіт, без запису в базу")
    def import_template_cmd(path, daily_dir, start, end, first_week, name, dry_run):
        """Імпортувати постійний розклад з аркуша «Шаблон» і зробити його активним."""
        from .scheduler.semester import Semester
        from .template_import import import_template

        semester = Semester(start.date(), end.date(), int(first_week))
        report = import_template(path, semester, name or f"Розклад з шаблону ({path.stem})",
                                 daily_dir or path.parent, dry_run)
        for line in report.lines():
            click.echo(line)
        click.echo("Пробний запуск — у базу нічого не записано" if dry_run else "Розклад опубліковано як активний")

    @app.cli.command("import-rules")
    @click.argument("path", required=False)
    def import_rules(path):
        """Імпортувати вподобання викладачів з JSON (за замовчуванням config/teacher_rules.json)."""
        from .views_admin import import_rules_file

        click.echo(f"Імпортовано правил: {import_rules_file(path or app.config['RULES_FILE'])}")

    @app.cli.command("seed-demo")
    @click.option("--password", default="demo-parol", show_default=True)
    def seed_demo(password):
        """Демо-акаунти: адмін і кілька викладачів з активного розкладу."""
        from .timetable import teachers_of

        def add(email, name, role, teacher=None):
            if not db.session.scalar(select(db.User).where(db.User.email == email)):
                db.session.add(db.User(email=email, full_name=name, password_hash=hash_password(password),
                                       role=role, teacher_name=teacher))
                click.echo(f"  {email} ({role}{', ' + teacher if teacher else ''})")

        add("admin@rcit.ukr.education", "Навчальна частина", db.User.ADMIN)
        tt = db.active_timetable()
        if tt is None:
            click.echo("Активного розкладу немає — викладачів не створено (спершу опублікуйте розклад)")
        else:
            for i, teacher in enumerate(teachers_of(tt)[:5], 1):
                add(f"teacher{i}@rcit.ukr.education", teacher, db.User.TEACHER, teacher)
        db.session.commit()
        click.echo(f"Пароль усіх демо-акаунтів: {password}")

    return app
