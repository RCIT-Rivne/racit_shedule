"""Призначення аудиторій після побудови розкладу.

Кожне заняття отримує тип аудиторії за назвою дисципліни:
  * фізкультура — спортзал (може бути кілька груп одночасно);
  * «Захист України» — тир, у крайньому разі «н.м» або звичайна аудиторія;
  * комп'ютерні дисципліни — комп'ютерні класи (для КН/ІПЗ також аудиторії
    «тільки для програмістів»), інакше звичайна аудиторія;
  * решта — звичайні аудиторії, у крайньому разі резервні.
Мета — щоб кожен викладач працював у якомога меншій кількості аудиторій
(«свій кабінет»): жадібно, від найзавантаженіших викладачів, кожному —
аудиторія, вільна в більшості його пар; потім «ремонт» занять без аудиторії. Однакове заняття в чисельнику і знаменнику — в одній
аудиторії. Для підгруп (кілька викладачів) — кілька аудиторій.
"""
from __future__ import annotations

from collections import defaultdict

from .models import Placement, Room, Schedule

PROGRAMMER_GROUPS = ("КН", "ІПЗ")

COMPUTER_KEYWORDS = (
    "інформат",
    "програм",
    "комп'ютер",
    "комп’ютер",
    "бази даних",
    "web",
    "веб",
    "операційні системи",
    "алгоритм",
    "цифров",
    "тестування",
    "штучний інтелект",
    "мультимедій",
    "геймдизайн",
    "криптограф",
    "інформаційні системи",
    "аналізу даних",
    "автоматизованих систем",
    "захисту інформації",
    "інструментальні засоби",
    "обробка інформації",
)


def room_category(subject: str) -> str:
    s = subject.lower()
    if "фізична культура" in s or "фізичне виховання" in s:
        return "gym"
    if "захист україни" in s or "національного спротиву" in s:
        return "range"
    if any(k in s for k in COMPUTER_KEYWORDS):
        return "computer"
    return "regular"


# Вартість аудиторії за рівнем пріоритету.
COST_PINNED = -10  # закріплена аудиторія викладача (правило room)
COST_PREFERRED = 0
COST_FALLBACK = 20
COST_RESERVE = 60
COST_LAST_RESORT = 200


def room_options(category: str, group: str, rooms_by_kind: dict[str, list[Room]]) -> list[tuple[int, Room]]:
    """Допустимі аудиторії для заняття з вартістю (менше — краще)."""
    tiers: list[tuple[int, list[Room]]]
    reserve = rooms_by_kind["reserve"] + rooms_by_kind["range_reserve"]
    last = rooms_by_kind["lab"] + rooms_by_kind["other"]
    if category == "gym":
        tiers = [(COST_PREFERRED, rooms_by_kind["gym"])]
    elif category == "range":
        tiers = [
            (COST_PREFERRED, rooms_by_kind["range"]),
            (COST_FALLBACK, rooms_by_kind["range_reserve"] + rooms_by_kind["regular"]),
        ]
    elif category == "computer":
        is_prog = group.split("-")[0] in PROGRAMMER_GROUPS
        first = rooms_by_kind["computer"] + (rooms_by_kind["programmers"] if is_prog else [])
        tiers = [
            (COST_PREFERRED, first),
            (COST_FALLBACK, rooms_by_kind["regular"]),
            (COST_RESERVE, reserve),
            (COST_LAST_RESORT, last),
        ]
    else:
        tiers = [(COST_PREFERRED, rooms_by_kind["regular"]), (COST_RESERVE, reserve), (COST_LAST_RESORT, last)]
    seen, out = set(), []
    for cost, rooms in tiers:
        for r in rooms:
            if r.name not in seen:
                seen.add(r.name)
                out.append((cost, r))
    return out


def classroom_capacity(rooms: list[Room]) -> int:
    """Скільки занять (крім фізкультури) можна провести одночасно."""
    return sum(1 for r in rooms if r.kind not in ("gym", "other"))


def _home_room_greedy(items, busy_init=None) -> dict:
    """Жадібний «свій кабінет»: викладачі від найзавантаженіших; кожному —
    аудиторія найкращого рівня, вільна в найбільшій кількості його пар.

    items: {(position, i): (teacher, weeks, day, pair, [(cost, room), ...])}
    Повертає {(position, i): room_name | None}.
    """
    busy = set(busy_init or ())
    result: dict = {}
    by_teacher: dict = defaultdict(list)
    for key, (teacher, *_rest) in items.items():
        by_teacher[teacher].append(key)

    def free(room, key):
        _, weeks, day, pair, _ = items[key]
        return room.shared or all((room.name, w, day, pair) not in busy for w in weeks)

    def take(room, key):
        _, weeks, day, pair, _ = items[key]
        result[key] = room.name
        if not room.shared:
            for w in weeks:
                busy.add((room.name, w, day, pair))

    # Спершу викладачі з закріпленою аудиторією (мінусова вартість), далі — найзавантаженіші.
    def order(t):
        fixed = any(c < 0 for k in by_teacher[t] for c, _ in items[k][4])
        return (not fixed, -len(by_teacher[t]))

    for teacher in sorted(by_teacher, key=order):
        pending = list(by_teacher[teacher])
        while pending:
            # Кандидат: (вартість, -скільки пар покриває) — найкращий рівень, найбільше покриття.
            best = None
            for cost, room in {(c, r.name): (c, r) for k in pending for c, r in items[k][4]}.values():
                covered = [k for k in pending if any(r.name == room.name and c == cost for c, r in items[k][4])
                           and free(room, k)]
                if covered and (best is None or (cost, -len(covered)) < (best[0], -len(best[2]))):
                    best = (cost, room, covered)
            if best is None:
                for k in pending:
                    result[k] = None
                break
            _, room, covered = best
            for k in covered:
                # Підгрупи однієї позиції не можуть бути в одній аудиторії.
                if free(room, k):
                    take(room, k)
            pending = [k for k in pending if k not in result]
    return result


def _repair(items, result) -> None:
    """Заняття без аудиторії: звільняємо аудиторію, пересунувши інше заняття
    в цей самий час до іншої вільної аудиторії."""
    busy: dict = {}
    for key, room in result.items():
        if room:
            _, weeks, day, pair, _ = items[key]
            for w in weeks:
                busy[room, w, day, pair] = key

    def is_free(room, key, ignore=None):
        _, weeks, day, pair, _ = items[key]
        return room.shared or all(busy.get((room.name, w, day, pair)) in (None, ignore) for w in weeks)

    def move(key, room):
        _, weeks, day, pair, _ = items[key]
        old = result.get(key)
        for w in weeks:
            if old:
                busy.pop((old, w, day, pair), None)
            if not room.shared:
                busy[room.name, w, day, pair] = key
        result[key] = room.name

    for key in [k for k, r in result.items() if r is None]:
        _, weeks, day, pair, options = items[key]
        for _, room in options:
            blockers = {busy.get((room.name, w, day, pair)) for w in weeks} - {None}
            if not blockers:
                move(key, room)
                break
            if len(blockers) == 1:
                other = blockers.pop()
                alt = next((r for _, r in items[other][4] if r.name != room.name and is_free(r, other)), None)
                if alt:
                    move(other, alt)
                    move(key, room)
                    break


def assign_rooms(schedule: Schedule) -> None:
    """Заповнює Placement.rooms. Незаповнені аудиторії виявляє валідатор."""
    data = schedule.data
    if not data.rooms:
        return
    rooms_by_kind: dict[str, list[Room]] = defaultdict(list)
    for r in data.rooms:
        rooms_by_kind[r.kind].append(r)

    # Однакові заняття двох тижнів — одна «позиція» з однією аудиторією.
    positions: dict[tuple[int, int, int], list[Placement]] = defaultdict(list)
    for p in schedule.placements:
        positions[p.lesson_id, p.day, p.pair].append(p)

    # Кожна підгрупа (викладач) позиції — окремий елемент.
    by_name = {r.name: r for r in data.rooms}
    fixed_rooms: dict[str, tuple[list[Room], bool]] = {}
    for rule in data.rules:
        if rule.kind == "room" and rule.room in by_name:
            for t in rule.teachers:
                fixed_rooms[t] = ([by_name[rule.room]], rule.hard)

    items = {}
    for key, placements in positions.items():
        lid, day, pair = key
        lesson = schedule.lesson(lid)
        options = room_options(room_category(lesson.subject), lesson.group, rooms_by_kind)
        weeks = [p.week for p in placements]
        for i in range(max(1, len(lesson.teachers))):
            teacher = lesson.teachers[i] if i < len(lesson.teachers) else f"—{lesson.group}"
            opts = options
            if teacher in fixed_rooms:
                rooms, hard = fixed_rooms[teacher]
                pinned = [(COST_PINNED, r) for r in rooms]
                opts = pinned if hard else pinned + [(c, r) for c, r in options if r not in rooms]
            items[key, i] = (teacher, weeks, day, pair, opts)

    result = _home_room_greedy(items)
    _repair(items, result)

    for key, placements in positions.items():
        lesson = schedule.lesson(key[0])
        chosen = [result[key, i] for i in range(max(1, len(lesson.teachers))) if result.get((key, i))]
        for p in placements:
            p.rooms = list(chosen)
