"""Вхід, ролі та CSRF-захист форм.

Поки що — локальні акаунти (email + пароль). Вхід через Google
(@rcit.ukr.education) додасться окремим маршрутом, який так само кладе
user_id у сесію; решта застосунку від способу входу не залежить.
"""
from __future__ import annotations

import secrets
from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session as flask_session, url_for
from sqlalchemy import func, select
from werkzeug.security import check_password_hash, generate_password_hash

from .db import User, session

bp = Blueprint("auth", __name__)


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def load_user() -> None:
    user_id = flask_session.get("user_id")
    g.user = session.get(User, user_id) if user_id else None


def csrf_token() -> str:
    if "csrf" not in flask_session:
        flask_session["csrf"] = secrets.token_urlsafe(32)
    return flask_session["csrf"]


def check_csrf() -> None:
    if request.method == "POST":
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
        if not sent or not secrets.compare_digest(sent, flask_session.get("csrf", "")):
            abort(400, "Форма застаріла. Оновіть сторінку й спробуйте ще раз.")


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not g.user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def _safe_next(target: str | None) -> str | None:
    return target if target and target.startswith("/") and not target.startswith("//") else None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = session.scalar(select(User).where(func.lower(User.email) == email))
        if user and check_password_hash(user.password_hash, password):
            flask_session.clear()
            flask_session["user_id"] = user.id
            target = _safe_next(request.args.get("next"))
            if target:
                return redirect(target)
            return redirect(url_for("admin.dashboard" if user.is_admin else "teacher.home"))
        flash("Неправильний email або пароль")
    return render_template("auth/login.html")


@bp.post("/logout")
def logout():
    flask_session.clear()
    return redirect(url_for("public.home"))


def ensure_admin(email: str | None, password: str | None) -> None:
    """Створити першого адміністратора зі змінних середовища, якщо адмінів ще немає."""
    if not email or not password:
        return
    if session.scalar(select(User).where(User.role == User.ADMIN)):
        return
    session.add(User(email=email.lower(), full_name="Адміністратор", password_hash=hash_password(password), role=User.ADMIN))
    session.commit()
