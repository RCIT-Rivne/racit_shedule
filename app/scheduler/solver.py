"""Побудова розкладу за допомогою CP-SAT (Google OR-Tools).

Модель: булева змінна x[l, w, d, p] = 1, якщо заняття l (рядок навантаження)
стоїть у тижні w (0 — чисельник, 1 — знаменник), у день d (пн–пт), на парі p.
Дозволені пари групи: її зміна (І — 1–4, ІІ — 5–7, максимум 7 пар) і одна пара
сусідньої зміни (штрафується як «перехід між змінами»).
Субота не моделюється: вона повторює будній день за ротацією (semester.py).

Жорсткі обмеження (не порушуються ніколи):
  * у групи не більше одного заняття в одну пару і не більше 4 пар на день;
  * викладач не веде два заняття одночасно; у викладача не більше 4 пар на день;
  * кількість пар дисципліни за тиждень точно відповідає навантаженню;
    дробове навантаження чергується по тижнях (1,5 -> 1 і 2). Що саме —
    менше чи більше — у чисельнику, обирається для кожної дисципліни окремо,
    як у шаблоні коледжу: дві «половинки» ділять одну пару (Математика в
    чисельнику / Біологія в знаменнику), і тижні виходять рівними;
  * одночасних занять (крім фізкультури) не більше, ніж аудиторій;
  * жорсткі вподобання викладачів (TeacherRule, hard=True): не ставити в
    певні дні/пари, не більше N робочих днів на тиждень.

М'які обмеження (штрафи в цільовій функції, у порядку пріоритету з правил):
  * навчальний день групи — 3–4 пари без «вікон», без вільних днів;
  * без двох однакових дисциплін в один день;
  * без переходу між змінами;
  * у групи не більше одного дня на тиждень без першої пари зміни;
  * м'які вподобання викладачів («за можливості не першу пару», методичний день);
  * без «вікон» у викладачів;
  * рівномірне навантаження викладачів по днях;
  * чисельник і знаменник максимально схожі (однакова «форма» дня групи);
  * заняття починаються з першої пари зміни.

Пошук у два етапи: спершу найкращий розклад за правилами груп (core), потім,
не погіршуючи його, — якість для викладачів (penalties).
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from ortools.sat.python import cp_model

from .models import TOTAL_PAIRS, Placement, ProblemData, Schedule
from .rooms import classroom_capacity, room_category


@dataclass
class SolverConfig:
    time_limit: float = 120.0
    workers: int = 0  # 0 — усі ядра процесора
    max_pair: int = TOTAL_PAIRS  # остання можлива пара дня
    # Дозволити групі одну пару сусідньої зміни (І зміна — 5-та, ІІ — 4-та), зі штрафом.
    allow_cross_shift: bool = True
    min_pairs_per_day: int = 3
    max_pairs_per_day: int = 4
    max_teacher_pairs_per_day: int = 4
    # Для занять з цілим навантаженням чисельник і знаменник однакові.
    same_weeks_for_integer: bool = True
    seed: int = 0
    stage1_share: float = 0.4  # частка часу на етап 1

    w_under_min: int = 1000
    w_gap: int = 1000
    w_duplicate: int = 300
    w_cross_shift: int = 200
    w_late_day: int = 500
    w_pref_days: int = 150
    w_pref_avoid: int = 60
    # Скільки днів на тиждень група може починати не з першої пари зміни.
    max_late_days: int = 1
    w_free_day: int = 100
    w_teacher_window: int = 30
    w_week_shape: int = 20
    w_teacher_balance: int = 5
    w_computer_rooms: int = 50
    w_week_diff: int = 2
    w_stability: int = 3  # за кожну пару, зрушену відносно попереднього розкладу (лише з hint)
    w_late_start: int = 1


ProgressFn = Callable[[dict], None]

PENALTY_NAMES = {
    "w_under_min": "Менше 3 пар у групи",
    "w_gap": "Вікна у групи",
    "w_free_day": "Вільні дні у групи",
    "w_cross_shift": "Пари поза своєю зміною",
    "w_late_day": "Групи без першої пари частіше 1 разу на тиждень",
    "w_pref_days": "Вподобання: зайві робочі дні викладачів",
    "w_pref_avoid": "Вподобання: небажані пари викладачів",
    "w_duplicate": "Однакові дисципліни в день",
    "w_teacher_window": "Вікна викладачів",
    "w_week_shape": "Різна форма чисельника і знаменника",
    "w_teacher_balance": "Нерівномірне навантаження викладачів",
    "w_week_diff": "Різні місця дробових дисциплін",
    "w_late_start": "Початок не з першої пари зміни",
    "w_computer_rooms": "Нестача комп'ютерних класів",
    "w_stability": "Зрушені пари відносно попереднього розкладу",
}


def _idle_slots(model: cp_model.CpModel, busy: list) -> list:
    """Змінні «вікно» для послідовності зайнятості (0/1): idle_p = 1, якщо пара p
    вільна, але до неї і після неї є заняття. Сума = довжина вікон."""
    n = len(busy)
    before = [model.new_bool_var("") for _ in range(n)]
    after = [model.new_bool_var("") for _ in range(n)]
    for p in range(n):
        model.add(before[p] >= busy[p])
        model.add(after[p] >= busy[p])
        if p:
            model.add(before[p] >= before[p - 1])
        if p < n - 1:
            model.add(after[p] >= after[p + 1])
    idle = []
    for p in range(1, n - 1):
        v = model.new_bool_var("")
        model.add(v >= before[p - 1] + after[p + 1] - 1 - busy[p])
        idle.append(v)
    return idle


def _make_solver(config: SolverConfig, time_limit: float) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = config.workers
    solver.parameters.random_seed = config.seed
    return solver


class _Progress(cp_model.CpSolverSolutionCallback):
    def __init__(self, fn: ProgressFn | None):
        super().__init__()
        self._fn = fn
        self._start = time.time()
        self.solutions = 0
        self.stage = 1

    def on_solution_callback(self):
        self.solutions += 1
        if self._fn:
            self._fn(
                {
                    "solutions": self.solutions,
                    "stage": self.stage,
                    "objective": self.ObjectiveValue(),
                    "bound": self.BestObjectiveBound(),
                    "elapsed": round(time.time() - self._start, 1),
                }
            )


def solve(
    data: ProblemData,
    config: SolverConfig | None = None,
    progress: ProgressFn | None = None,
    hint: list[Placement] | None = None,
) -> Schedule:
    config = config or SolverConfig()
    model = cp_model.CpModel()
    n_days = len(data.days)
    weeks = (0, 1)
    groups = {g.name: g for g in data.groups}
    group_pairs = {g.name: g.allowed_pairs(config.max_pair, config.allow_cross_shift) for g in data.groups}

    # x[l, w, d, p]: p — абсолютний номер пари з дозволених для групи.
    x: dict[tuple[int, int, int, int], cp_model.IntVar] = {}
    for l in data.lessons:
        shared = config.same_weeks_for_integer and not l.is_fractional
        for d in range(n_days):
            for p in group_pairs[l.group]:
                v0 = model.new_bool_var(f"x_{l.id}_0_{d}_{p}")
                x[l.id, 0, d, p] = v0
                x[l.id, 1, d, p] = v0 if shared else model.new_bool_var(f"x_{l.id}_1_{d}_{p}")

    def lesson_vars(l, w, d):
        return [(p, x[l.id, w, d, p]) for p in group_pairs[l.group]]

    # core — правила груп (3–4 пари, вікна, дублі, зміна); penalties — якість
    # (викладачі, схожість тижнів, ранній початок).
    core: list[tuple[str, int, cp_model.LinearExprT]] = []
    penalties: list[tuple[str, int, cp_model.LinearExprT]] = []

    # --- Вподобання викладачів: коли не ставити (жорстко або «за можливості») ---
    blocked: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for rule in data.rules:
        if rule.kind != "unavailable":
            continue
        cells = rule.cells(n_days, config.max_pair)
        for l in data.lessons:
            if not set(rule.teachers) & set(l.teachers):
                continue
            for d, p in cells:
                if d < n_days and p in group_pairs[l.group]:
                    if rule.hard:
                        blocked[l.id].add((d, p))
                    for w in weeks:
                        if rule.hard:
                            model.add(x[l.id, w, d, p] == 0)
                        else:
                            penalties.append(("w_pref_avoid", config.w_pref_avoid, x[l.id, w, d, p]))

    def open_days(l) -> int:
        """Скільки днів заняття взагалі можна поставити (з урахуванням жорстких вподобань)."""
        return sum(any((d, p) not in blocked[l.id] for p in group_pairs[l.group]) for d in range(n_days))

    # --- Закріплена аудиторія (жорстке правило room): одночасно лише одне заняття ---
    shared_rooms = {r.name for r in data.rooms if r.shared}
    pinned: dict[str, set[int]] = defaultdict(set)
    for rule in data.rules:
        if rule.kind == "room" and rule.hard and rule.room not in shared_rooms:
            pinned[rule.room] |= {l.id for l in data.lessons if rule.applies_to(l)}
    for lesson_ids in pinned.values():
        for w in weeks:
            for d in range(n_days):
                for p in range(1, config.max_pair + 1):
                    vars_ = [x[i, w, d, p] for i in lesson_ids if (i, w, d, p) in x]
                    if len(vars_) > 1:
                        model.add(sum(vars_) <= 1)


    # --- Тижневе навантаження дисципліни ---
    for l in data.lessons:
        lo, hi = l.week_bounds
        counts = [sum(v for d in range(n_days) for _, v in lesson_vars(l, w, d)) for w in weeks]
        if lo == hi:
            model.add(counts[0] == lo)
        else:
            for c in counts:
                model.add(c >= lo)
                model.add(c <= hi)
            model.add(counts[0] + counts[1] == l.total_two_weeks)
            # Чисельник і знаменник схожі: спільні пари на тих самих місцях.
            for d in range(n_days):
                for p in group_pairs[l.group]:
                    diff = model.new_bool_var("")
                    model.add(diff >= x[l.id, 0, d, p] - x[l.id, 1, d, p])
                    model.add(diff >= x[l.id, 1, d, p] - x[l.id, 0, d, p])
                    penalties.append(("w_week_diff", config.w_week_diff, diff))

    # --- Групи ---
    by_group: dict[str, list] = defaultdict(list)
    for l in data.lessons:
        by_group[l.group].append(l)

    for group, lessons in by_group.items():
        pairs = group_pairs[group]
        shift_pairs = set(groups[group].pair_numbers(config.max_pair))
        first_pair = min(shift_pairs)
        by_subject: dict[str, list] = defaultdict(list)
        for l in lessons:
            by_subject[l.subject_key].append(l)

        occupancy = {}
        late_days: dict[int, list] = {w: [] for w in weeks}
        for w in weeks:
            for d in range(n_days):
                occ = []
                for p in pairs:
                    y = sum(x[l.id, w, d, p] for l in lessons)
                    model.add(y <= 1)
                    occ.append(y)
                occupancy[w, d] = occ

                n = sum(occ)
                works = model.new_bool_var("")
                model.add(n <= config.max_pairs_per_day * works)
                model.add(n >= works)
                core.append(("w_free_day", config.w_free_day, 1 - works))
                under = model.new_int_var(0, config.min_pairs_per_day, "")
                model.add(under >= config.min_pairs_per_day * works - n)
                core.append(("w_under_min", config.w_under_min, under))

                # «Вікна»: початок блоку занять — пара зайнята, а попередня ні.
                starts = []
                for i in range(len(pairs)):
                    s = model.new_bool_var("")
                    model.add(s >= occ[i] - (occ[i - 1] if i else 0))
                    starts.append(s)
                gap = model.new_int_var(0, len(pairs), "")
                model.add(sum(starts) <= 1 + gap)
                core.append(("w_gap", config.w_gap, gap))
                late = [s for s, p in zip(starts, pairs) if p > first_pair]
                if late:
                    penalties.append(("w_late_start", config.w_late_start, sum(late)))
                # День без першої пари зміни (навчальний, але починається пізніше).
                no_first = model.new_bool_var("")
                model.add(no_first >= works - sum(y for y, p in zip(occ, pairs) if p <= first_pair))
                late_days[w].append(no_first)

                # Пара поза своєю зміною («перехід між змінами») — небажано.
                for y, p in zip(occ, pairs):
                    if p not in shift_pairs:
                        core.append(("w_cross_shift", config.w_cross_shift, y))

                # Однакові дисципліни в один день: жорстко, якщо пар дисципліни
                # за тиждень не більше, ніж доступних викладачу днів; інакше дубль
                # неминучий — штраф.
                for subject_lessons in by_subject.values():
                    total = sum(v for l in subject_lessons for _, v in lesson_vars(l, w, d))
                    days_open = min(open_days(l) for l in subject_lessons)
                    if sum(l.week_bounds[w] for l in subject_lessons) <= days_open:
                        model.add(total <= 1)
                    else:
                        dup = model.new_int_var(0, len(pairs), "")
                        model.add(total <= 1 + dup)
                        core.append(("w_duplicate", config.w_duplicate, dup))

        # Перші пари обов'язкові: не більше max_late_days днів на тиждень без першої пари.
        for w in weeks:
            extra = model.new_int_var(0, n_days, "")
            model.add(sum(late_days[w]) <= config.max_late_days + extra)
            core.append(("w_late_day", config.w_late_day, extra))

        # Однакова «форма» дня в чисельнику і знаменнику (різниця — лише
        # в тому, ЯКА дисципліна стоїть, а не ЧИ стоїть пара).
        if any(l.is_fractional for l in lessons):
            for d in range(n_days):
                for a, b in zip(occupancy[0, d], occupancy[1, d]):
                    diff = model.new_bool_var("")
                    model.add(diff >= a - b)
                    model.add(diff >= b - a)
                    penalties.append(("w_week_shape", config.w_week_shape, diff))

    # --- Викладачі ---
    by_teacher: dict[str, list] = defaultdict(list)
    for l in data.lessons:
        for t in l.teachers:
            by_teacher[t].append(l)

    for teacher, lessons in by_teacher.items():
        weekly_max = sum(l.week_bounds[1] for l in lessons)
        ideal = math.ceil(weekly_max / n_days)
        max_days_rules = data.rules_for(teacher, "max_days")
        teacher_days: dict[int, list] = {w: [] for w in weeks}
        for w in weeks:
            for d in range(n_days):
                per_pair: dict[int, list] = defaultdict(list)
                for l in lessons:
                    for p, v in lesson_vars(l, w, d):
                        per_pair[p].append(v)
                for vars_ in per_pair.values():
                    if len(vars_) > 1:
                        model.add(sum(vars_) <= 1)
                day_load = sum(v for vs in per_pair.values() for v in vs)
                model.add(day_load <= config.max_teacher_pairs_per_day)
                if max_days_rules:
                    works_day = model.new_bool_var("")
                    model.add(day_load <= config.max_teacher_pairs_per_day * works_day)
                    teacher_days[w].append(works_day)
                over = model.new_int_var(0, config.max_teacher_pairs_per_day, "")
                model.add(over >= day_load - ideal)
                penalties.append(("w_teacher_balance", config.w_teacher_balance, over))

                # Вікна викладача між його парами (обидві зміни).
                used = sorted(per_pair)
                if len(lessons) > 1 and len(used) > 2:
                    busy = [sum(per_pair.get(p, [])) for p in range(used[0], used[-1] + 1)]
                    for v in _idle_slots(model, busy):
                        penalties.append(("w_teacher_window", config.w_teacher_window, v))

        # Не більше N робочих днів на тиждень (один день для Бялика, методичний день тощо).
        for rule in max_days_rules:
            for w in weeks:
                if rule.hard:
                    model.add(sum(teacher_days[w]) <= rule.value)
                else:
                    extra = model.new_int_var(0, n_days, "")
                    model.add(sum(teacher_days[w]) <= rule.value + extra)
                    penalties.append(("w_pref_days", config.w_pref_days, extra))

    # --- Аудиторії: не більше одночасних занять, ніж є аудиторій ---
    if data.rooms:
        capacity = classroom_capacity(data.rooms)
        computer_rooms = sum(1 for r in data.rooms if r.kind in ("computer", "programmers"))
        demand: dict[tuple, list] = defaultdict(list)
        computer_demand: dict[tuple, list] = defaultdict(list)
        for l in data.lessons:
            category = room_category(l.subject)
            if category == "gym":
                continue
            need = max(1, len(l.teachers))
            for w in weeks:
                for d in range(n_days):
                    for p, v in lesson_vars(l, w, d):
                        demand[w, d, p].append(need * v)
                        if category == "computer":
                            computer_demand[w, d, p].append(need * v)
        for terms in demand.values():
            model.add(sum(terms) <= capacity)
        for terms in computer_demand.values():
            if len(terms) > computer_rooms:
                excess = model.new_int_var(0, len(terms) * 2, "")
                model.add(excess >= sum(terms) - computer_rooms)
                penalties.append(("w_computer_rooms", config.w_computer_rooms, excess))

    # Підказка: попередній розклад як стартова точка (щоб новий був схожим на нього).
    if hint:
        placed = {(p.lesson_id, p.week, p.day, p.pair) for p in hint}
        values: dict[int, tuple] = {}  # одна змінна може стояти за обидва тижні
        for key, var in x.items():
            prev = values.get(var.index, (var, False))
            values[var.index] = (var, prev[1] or key in placed)
        for var, value in values.values():
            model.add_hint(var, value)
            # Стабільність: кожна пара, зрушена з попереднього місця, — невеликий штраф.
            if value and config.w_stability:
                penalties.append(("w_stability", config.w_stability, 1 - var))

    core_expr = sum(w * p for _, w, p in core)
    quality_expr = sum(w * p for _, w, p in penalties)
    callback = _Progress(progress)
    started = time.time()

    # Етап 1: розклад, найкращий за правилами груп.
    model.minimize(core_expr)
    solver = _make_solver(config, config.time_limit * config.stage1_share)
    status = solver.solve(model, callback)

    # Етап 2: не погіршуючи етап 1, покращуємо якість (вікна викладачів тощо),
    # стартуючи з уже знайденого розв'язку. Якщо не встигли — лишається етап 1.
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        model.add(core_expr <= int(solver.objective_value))
        # Повна підказка (усі змінні), щоб етап 2 одразу мав розв'язок етапу 1.
        model.clear_hints()
        for i in range(len(model.proto.variables)):
            var = model.get_int_var_from_proto_index(i)
            model.add_hint(var, solver.value(var))
        model.minimize(core_expr + quality_expr)
        remaining = max(config.time_limit - (time.time() - started), 1.0)
        stage2_solver = _make_solver(config, remaining)
        callback.stage = 2
        stage2 = stage2_solver.solve(model, callback)
        if stage2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            solver, status = stage2_solver, stage2
    elapsed = time.time() - started

    status_name = solver.status_name(status)
    placements: list[Placement] = []
    objective = None  # повний штраф (сума розбивки), навіть якщо етап 2 не встиг
    breakdown: dict[str, int] = {}
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for name, w, expr in core + penalties:
            value = w * solver.value(expr)
            if value:
                label = PENALTY_NAMES.get(name, name)
                breakdown[label] = breakdown.get(label, 0) + value
        objective = sum(breakdown.values())
        for (lid, w, d, p), var in x.items():
            if solver.boolean_value(var):
                placements.append(Placement(lid, w, d, p))

    return Schedule(
        data=data,
        placements=placements,
        status=status_name,
        objective=objective,
        solve_seconds=round(elapsed, 1),
        penalty_breakdown=dict(sorted(breakdown.items(), key=lambda kv: -kv[1])),
    )
