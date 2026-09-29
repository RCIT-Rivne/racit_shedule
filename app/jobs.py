"""Фонові задачі генерації розкладу.

Кожна задача зберігається в DATA_DIR/jobs/<id>/:
  meta.json      — статус, параметри, прогрес
  input.xlsx     — вхідний файл
  result.json    — розклад для перегляду у браузері
  schedule.xlsx  — експорт
Задачі виконуються по одній (розв'язувач і так використовує всі ядра).
"""
from __future__ import annotations

import json
import pickle
import shutil
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .scheduler import InputError, Semester, SolverConfig, TeacherRule, generate
from .scheduler.exporter import export_xlsx
from .scheduler.metrics import quality
from .scheduler.models import Schedule


class JobStore:
    def __init__(self, data_dir: Path, workers: int = 0):
        self.root = Path(data_dir) / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.solver_workers = workers
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._lock = threading.Lock()
        self._mark_interrupted()

    def _mark_interrupted(self):
        """Задачі, що «висіли» при перезапуску, позначаємо як перервані."""
        for job in self.list():
            if job["status"] in ("queued", "running"):
                self._update(job["id"], status="failed", error="Задачу перервано перезапуском сервера")

    # --- зберігання ---
    def path(self, job_id: str) -> Path:
        if not job_id.isalnum():
            raise KeyError(job_id)
        return self.root / job_id

    def get(self, job_id: str) -> dict | None:
        meta = self.path(job_id) / "meta.json"
        if not meta.exists():
            return None
        return json.loads(meta.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        jobs = [self.get(p.name) for p in self.root.iterdir() if p.is_dir()]
        return sorted((j for j in jobs if j), key=lambda j: j["created"], reverse=True)

    def schedule(self, job_id: str) -> Schedule | None:
        """Збережений розклад (файли пише лише сам застосунок)."""
        f = self.path(job_id) / "schedule.pkl"
        if not f.exists():
            return None
        with open(f, "rb") as fh:
            return pickle.load(fh)

    def result(self, job_id: str) -> dict | None:
        f = self.path(job_id) / "result.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def _update(self, job_id: str, **fields):
        with self._lock:
            meta = self.get(job_id) or {}
            meta.update(fields)
            (self.path(job_id) / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
            )

    def delete(self, job_id: str):
        job = self.get(job_id)
        if job and job["status"] not in ("queued", "running"):
            shutil.rmtree(self.path(job_id))

    # --- запуск ---
    def submit(
        self,
        input_file: Path,
        input_name: str,
        time_limit: float,
        semester: Semester,
        rules: list[dict] | None = None,
        hint_entries: list[dict] | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex[:12]
        folder = self.path(job_id)
        folder.mkdir(parents=True)
        shutil.copy(input_file, folder / "input.xlsx")
        self._update(
            job_id,
            id=job_id,
            created=datetime.now().isoformat(timespec="seconds"),
            input_name=input_name,
            time_limit=time_limit,
            semester=semester.to_dict(),
            constraints=len(rules or []),
            based_on_active=bool(hint_entries),
            status="queued",
            progress=None,
        )
        self._executor.submit(self._run, job_id, time_limit, semester, rules or [], hint_entries)
        return job_id

    def _run(self, job_id: str, time_limit: float, semester: Semester, rules: list[dict], hint_entries=None):
        folder = self.path(job_id)
        self._update(job_id, status="running", started=datetime.now().isoformat(timespec="seconds"))
        try:
            config = SolverConfig(time_limit=time_limit, workers=self.solver_workers)
            schedule = generate(
                folder / "input.xlsx",
                config,
                lambda p: self._update(job_id, progress=p),
                semester=semester,
                rules=[TeacherRule.from_dict(r) for r in rules],
                hint_entries=hint_entries,
            )
            if schedule.placements:
                export_xlsx(schedule, folder / "schedule.xlsx")
                # Повний об'єкт розкладу — для експорту на довільну дату.
                with open(folder / "schedule.pkl", "wb") as f:
                    pickle.dump(schedule, f)
            (folder / "result.json").write_text(
                json.dumps(serialize(schedule), ensure_ascii=False), encoding="utf-8"
            )
            counts = {s: sum(1 for i in schedule.issues if i.severity == s) for s in ("error", "warning", "info")}
            self._update(
                job_id,
                status="done" if schedule.placements else "failed",
                finished=datetime.now().isoformat(timespec="seconds"),
                solver_status=schedule.status,
                objective=schedule.objective,
                solve_seconds=schedule.solve_seconds,
                issue_counts=counts,
                error=None if schedule.placements else "Розклад не знайдено",
            )
        except InputError as e:
            self._update(job_id, status="failed", error=str(e))
        except Exception as e:  # noqa: BLE001 — показуємо користувачу будь-яку помилку
            traceback.print_exc()
            self._update(job_id, status="failed", error=f"{type(e).__name__}: {e}")


def serialize(schedule: Schedule) -> dict:
    lessons = {l.id: l for l in schedule.data.lessons}
    return {
        "status": schedule.status,
        "objective": schedule.objective,
        "days": schedule.data.days,
        "saturdays": [
            {"date": s["date"].isoformat(), "day": s["day"], "week": s["week"]}
            for s in schedule.semester.saturdays_list()
        ],
        "penalty_breakdown": schedule.penalty_breakdown,
        "quality": quality(schedule),
        "groups": [{"name": g.name, "shift": g.shift, "course": g.course} for g in schedule.data.groups],
        "teachers": schedule.data.teachers,
        "placements": [
            {
                "group": lessons[p.lesson_id].group,
                "subject": lessons[p.lesson_id].subject,
                "teachers": lessons[p.lesson_id].teachers,
                "rooms": p.rooms,
                "week": p.week,
                "day": p.day,
                "pair": p.pair,
            }
            for p in schedule.placements
        ],
        "issues": [{"severity": i.severity, "rule": i.rule, "message": i.message} for i in schedule.issues],
    }
