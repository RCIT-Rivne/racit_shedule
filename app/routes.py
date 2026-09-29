from __future__ import annotations

import tempfile
from collections import defaultdict
from datetime import date
from io import BytesIO
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from .jobs import JobStore
from .scheduler.exporter import build_day_workbook, day_title
from .scheduler.models import DAYS, TOTAL_PAIRS, WEEKS
from .scheduler.semester import Semester

bp = Blueprint("main", __name__)


def jobs() -> JobStore:
    return current_app.extensions["jobs"]


def _job_or_404(job_id: str) -> dict:
    try:
        job = jobs().get(job_id)
    except KeyError:
        job = None
    if job is None:
        abort(404)
    return job


@bp.get("/")
def index():
    sample = current_app.config["SAMPLE_INPUT"]
    return render_template(
        "index.html",
        jobs=jobs().list(),
        sample_available=sample.exists(),
        sample_name=sample.name,
        default_time=int(current_app.config["DEFAULT_TIME_LIMIT"]),
        semester=Semester.default(),
    )


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _semester_from_form() -> Semester | None:
    default = Semester.default()
    start = _parse_date(request.form.get("semester_start")) or default.start
    end = _parse_date(request.form.get("semester_end")) or default.end
    if end < start:
        return None
    first_week = 1 if request.form.get("first_week") == "1" else 0
    return Semester(start, end, first_week, saturdays=request.form.get("saturdays") == "on")


@bp.post("/generate")
def generate():
    time_limit = min(max(request.form.get("time_limit", type=float) or 60, 5), 1800)
    semester = _semester_from_form()
    if semester is None:
        flash("Кінець семестру раніше за початок")
        return redirect(url_for("main.index"))
    upload = request.files.get("file")
    if upload and upload.filename:
        if not upload.filename.lower().endswith(".xlsx"):
            flash("Потрібен файл .xlsx")
            return redirect(url_for("main.index"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.xlsx"
            upload.save(path)
            job_id = jobs().submit(path, upload.filename, time_limit, semester)
    else:
        sample = current_app.config["SAMPLE_INPUT"]
        if not sample.exists():
            flash("Завантажте файл з навантаженням")
            return redirect(url_for("main.index"))
        job_id = jobs().submit(sample, sample.name, time_limit, semester)
    return redirect(url_for("main.job", job_id=job_id))


def _grid(result: dict, names: list[str], key: str):
    """{name: {(day, pair): [чисельник, знаменник]}}"""
    grid: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: [None, None]))
    for p in result["placements"]:
        if key == "teacher":
            for i, teacher in enumerate(p["teachers"]):
                if teacher in names:
                    # У підгрупах кожен викладач — у своїй аудиторії.
                    rooms = [p["rooms"][i]] if i < len(p["rooms"]) else p["rooms"]
                    grid[teacher][p["day"], p["pair"]][p["week"]] = {**p, "rooms": rooms}
        elif p["group"] in names:
            grid[p["group"]][p["day"], p["pair"]][p["week"]] = p
    return grid


@bp.get("/jobs/<job_id>")
def job(job_id: str):
    job = _job_or_404(job_id)
    result = jobs().result(job_id) if job["status"] == "done" else None
    semester = Semester.from_dict(job.get("semester"))
    ctx = dict(job=job, result=result, semester=semester, weeks=WEEKS, pairs=range(1, TOTAL_PAIRS + 1))
    if result:
        view = request.args.get("view", "groups")
        courses = sorted({g["course"] for g in result["groups"]})
        # Розклад на дату: один день і лише актуальний тиждень.
        on_date = _parse_date(request.args.get("date"))
        ctx.update(on_date=on_date, on_date_text=semester.describe(on_date) if on_date else "",
                   resolved=semester.resolve(on_date) if on_date else None, days_labels=DAYS,
                   saturdays=result.get("saturdays", []))
        if view == "teachers":
            selected = request.args.get("teacher") or (result["teachers"][0] if result["teachers"] else "")
            names = [selected]
        else:
            course = request.args.get("course", type=int) or (courses[0] if courses else 0)
            names = [g["name"] for g in result["groups"] if g["course"] == course]
            selected = course
        ctx.update(
            days=result.get("days", DAYS),
            view=view,
            courses=courses,
            selected=selected,
            names=names,
            grid=_grid(result, names, view[:-1]),
        )
    return render_template("job.html", **ctx)


@bp.get("/jobs/<job_id>/schedule.xlsx")
def download(job_id: str):
    job = _job_or_404(job_id)
    path = jobs().path(job_id) / "schedule.xlsx"
    if not path.exists():
        abort(404)
    name = Path(job["input_name"]).stem
    return send_file(path.resolve(), as_attachment=True, download_name=f"Розклад ({name}).xlsx")


@bp.get("/jobs/<job_id>/day.xlsx")
def download_day(job_id: str):
    _job_or_404(job_id)
    on_date = _parse_date(request.args.get("date"))
    schedule = jobs().schedule(job_id)
    if on_date is None or schedule is None:
        abort(404)
    buffer = BytesIO()
    build_day_workbook(schedule, on_date).save(buffer)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"{day_title(schedule, on_date)}.xlsx")


@bp.get("/jobs/<job_id>/input.xlsx")
def download_input(job_id: str):
    job = _job_or_404(job_id)
    return send_file((jobs().path(job_id) / "input.xlsx").resolve(), as_attachment=True, download_name=job["input_name"])


@bp.post("/jobs/<job_id>/delete")
def delete(job_id: str):
    _job_or_404(job_id)
    jobs().delete(job_id)
    return redirect(url_for("main.index"))


@bp.get("/sample.xlsx")
def sample():
    path = current_app.config["SAMPLE_INPUT"]
    if not path.exists():
        abort(404)
    return send_file(path.resolve(), as_attachment=True, download_name=path.name)
