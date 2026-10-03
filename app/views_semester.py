"""«Новий семестр» (адмін): імпорт готового розкладу з шаблону, шаблони файлів,
перехід до генерації."""
from __future__ import annotations

import json
import shutil
import time
import uuid
from datetime import date
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from sqlalchemy import select

from .auth import admin_required
from .db import Teacher, active_timetable, session
from .input_templates import grid_template, load_template
from .scheduler.models import DAYS, uk_sort_key
from .scheduler.semester import Semester
from .template_import import DAILY_NAME, build, import_template
from .timetable import groups_of

bp = Blueprint("semester", __name__, url_prefix="/admin/semester")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _uploads() -> Path:
    return current_app.config["DATA_DIR"] / "imports"


def _upload_dir(token: str) -> Path:
    if not token.isalnum():
        abort(404)
    path = _uploads() / token
    if not (path / "meta.json").exists():
        abort(404)
    return path


def _drop_stale_uploads(max_age: float = 24 * 3600) -> None:
    """Перевірені, але не опубліковані й не скасовані шаблони — видаляються через добу."""
    if not _uploads().exists():
        return
    for folder in _uploads().iterdir():
        if folder.is_dir() and time.time() - folder.stat().st_mtime > max_age:
            shutil.rmtree(folder, ignore_errors=True)


def _semester_from_form() -> Semester | None:
    default = Semester.default()
    try:
        start = date.fromisoformat(request.form.get("start") or default.start.isoformat())
        end = date.fromisoformat(request.form.get("end") or default.end.isoformat())
    except ValueError:
        return None
    if end < start:
        return None
    return Semester(start, end, 1 if request.form.get("first_week") == "1" else 0,
                    saturdays=request.form.get("saturdays") == "on")


def _teachers() -> list[tuple[str, str]]:
    rows = session.scalars(select(Teacher)).all()
    return sorted(((t.name, t.email or "") for t in rows), key=lambda r: uk_sort_key(r[0]))


@bp.get("")
@admin_required
def index():
    return render_template("admin/semester.html", semester=Semester.default(), active=active_timetable())


@bp.post("/preview")
@admin_required
def preview():
    semester = _semester_from_form()
    template = request.files.get("template")
    if semester is None:
        flash("Перевірте дати семестру")
        return redirect(url_for("semester.index"))
    if not template or not template.filename.lower().endswith(".xlsx"):
        flash("Завантажте файл шаблону .xlsx")
        return redirect(url_for("semester.index"))

    _drop_stale_uploads()
    token = uuid.uuid4().hex
    folder = _uploads() / token
    daily_dir = folder / "daily"
    daily_dir.mkdir(parents=True)
    template_path = folder / "template.xlsx"
    template.save(template_path)
    skipped = []
    for f in request.files.getlist("daily"):
        name = Path(f.filename or "").name
        if not name:
            continue
        if DAILY_NAME.match(name):
            f.save(daily_dir / name)
        else:
            skipped.append(name)

    try:
        report = build(template_path, daily_dir)
    except Exception as e:  # не той файл або пошкоджений
        shutil.rmtree(folder, ignore_errors=True)
        flash(f"Шаблон не вдалося прочитати: {e}")
        return redirect(url_for("semester.index"))

    meta = {"filename": Path(template.filename).name, "semester": semester.to_dict(),
            "name": request.form.get("name") or f"Розклад з шаблону ({Path(template.filename).stem})"}
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    per_week = [sum(1 for k in report.cells if k[0] == w) for w in (0, 1)]
    return render_template("admin/semester_preview.html", report=report, meta=meta, token=token,
                           semester=semester, skipped=skipped, per_week=per_week, days=DAYS)


@bp.post("/import/<token>")
@admin_required
def do_import(token: str):
    folder = _upload_dir(token)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    name = request.form.get("name") or meta["name"]
    # Ім'я файлу шаблону потрібне лише для позначки джерела розкладу.
    path = folder / meta["filename"]
    (folder / "template.xlsx").replace(path)
    import_template(path, Semester.from_dict(meta["semester"]), name, folder / "daily")
    shutil.rmtree(folder, ignore_errors=True)
    flash(f"Розклад «{name}» опубліковано й зроблено активним")
    return redirect(url_for("admin.timetables"))


@bp.post("/cancel/<token>")
@admin_required
def cancel(token: str):
    shutil.rmtree(_upload_dir(token), ignore_errors=True)
    return redirect(url_for("semester.index"))


@bp.get("/load-template.xlsx")
@admin_required
def download_load_template():
    example = request.args.get("empty") is None
    name = "Навантаження (приклад).xlsx" if example else "Навантаження (порожній шаблон).xlsx"
    return send_file(load_template(example), mimetype=XLSX, as_attachment=True, download_name=name)


@bp.get("/grid-template.xlsx")
@admin_required
def download_grid_template():
    tt = active_timetable()
    filled = request.args.get("filled") is not None and tt is not None
    groups = [g["name"] for g in groups_of(tt)] if tt else ["ГРУПА-1/1", "ГРУПА-1/2", "ГРУПА-2/1"]
    buf = grid_template(groups, tt.entries if filled else (), _teachers())
    name = "Розклад (Шаблон) — активний.xlsx" if filled else "Розклад (Шаблон).xlsx"
    return send_file(buf, mimetype=XLSX, as_attachment=True, download_name=name)
