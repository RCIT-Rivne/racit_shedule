"""Шаблони файлів для навчальної частини: навантаження для генератора і сітка «Шаблон»
для імпорту готового розкладу. Файли будуються на льоту, тож завжди відповідають
тому, що вміють читати `scheduler.loader` і `template_import`."""
from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .scheduler.models import DAYS
from .template_import import FIRST_ROW, PAIRS_PER_DAY, ROWS_PER_DAY

HEAD = Font(bold=True)
HEAD_FILL = PatternFill("solid", fgColor="E8EEF7")
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
THIN = Side(style="thin", color="999999")
DOTTED = Side(style="dotted", color="999999")

# Вигаданий, але розв'язний приклад: 3 групи, обидві зміни, підгрупи, дробове навантаження.
EXAMPLE_LOAD = [
    ("ПР-1/1", "Українська мова", "Коваленко О. П.", 2, 1),
    ("ПР-1/1", "Математика", "Мельник І. В.", 3, 1),
    ("ПР-1/1", "Іноземна мова", "Шевчук Н. С.", 2, 1),
    ("ПР-1/1", "Фізика", "Бондар Т. М.", 1.5, 1),
    ("ПР-1/1", "Хімія", "Бондар Т. М.", 1.5, 1),
    ("ПР-1/1", "Інформатика", "Ткаченко Р. О.\nКравець Л. А.", 2, 1),
    ("ПР-1/1", "Фізична культура", "Олійник В. Д.", 2, 1),
    ("ПР-1/1", "Історія України", "Коваленко О. П.", 2, 1),
    ("ПР-1/2", "Українська мова", "Коваленко О. П.", 2, 1),
    ("ПР-1/2", "Математика", "Мельник І. В.", 3, 1),
    ("ПР-1/2", "Іноземна мова", "Шевчук Н. С.", 2, 1),
    ("ПР-1/2", "Фізика", "Бондар Т. М.", 1.5, 1),
    ("ПР-1/2", "Хімія", "Бондар Т. М.", 1.5, 1),
    ("ПР-1/2", "Інформатика", "Кравець Л. А.", 2, 1),
    ("ПР-1/2", "Фізична культура", "Олійник В. Д.", 2, 1),
    ("ПР-1/2", "Історія України", "Коваленко О. П.", 2, 1),
    ("ПР-2/1", "Основи програмування", "Ткаченко Р. О.", 3, 2),
    ("ПР-2/1", "Бази даних", "Кравець Л. А.", 2, 2),
    ("ПР-2/1", "Вища математика", "Мельник І. В.", 2, 2),
    ("ПР-2/1", "Іноземна мова (за професійним спрямуванням)", "Шевчук Н. С.", 2, 2),
    ("ПР-2/1", "Вебтехнології", "Ткаченко Р. О.", 2, 2),
    ("ПР-2/1", "Фізична культура", "Олійник В. Д.", 1, 2),
]
EXAMPLE_ROOMS = [
    ("101", ""), ("102", ""), ("103", ""), ("104", ""), ("105", ""),
    ("201", "Комп'ютер"), ("202", "Комп'ютер"), ("106", "У крайніх випадках"), ("с/з", "Спортзал"),
]
EXAMPLE_TEACHERS = sorted({t for row in EXAMPLE_LOAD for t in row[2].split("\n")})

LOAD_INSTRUCTIONS = [
    "Як заповнити файл навантаження",
    "",
    "Аркуш «Навантаження» — один рядок на дисципліну групи. Назви колонок не змінюйте.",
    "• Група — шифр так, як у розкладі: КН-1/1, ІПЗ-2/2. Курс береться з цифри після дефіса.",
    "• Дисципліна — назва так, як вона має бути в розкладі.",
    "• Викладач — «Прізвище І. Б.». Кілька викладачів в одній клітинці (через Alt+Enter) — це підгрупи:",
    "  пара йде одночасно, кожна підгрупа зі своїм викладачем і в своїй аудиторії.",
    "• Кількість пар на тиждень — ціле або з половиною: 1,5 означає 1 пару в один тиждень і 2 в інший",
    "  (чисельник/знаменник чергуються).",
    "• Зміна — 1 (пари 1–4) або 2 (пари 5–7). Однакова в усіх рядках однієї групи.",
    "",
    "Обмеження, які перевіряються до генерації:",
    "• у групи не більше 4 пар на день, тобто не більше 20 пар на тиждень;",
    "• у викладача не більше 4 пар на день, тобто не більше 20 пар на тиждень.",
    "",
    "Аркуш «аудиторії» — колонка B: номер або назва, колонка C: примітка.",
    "Примітки, які розуміє генератор: «Комп'ютер», «Тільки для програмістів», «Лабораторія»,",
    "«У крайніх випадках» (резервна). Спортзал — «с/з», тир — «тир».",
    "",
    "Аркуш «Викладачі» — ПІБ і пошта коледжу (за поштою викладача знаходять журнал і бот).",
    "",
    "Вподобання викладачів (не можу в п'ятницю, лише 25 аудиторія тощо) задаються в адмінці:",
    "«Ще» → «Вподобання викладачів». Рядки-приклади з групами ПР-… видаліть.",
]

GRID_INSTRUCTIONS = [
    "Як заповнити шаблон готового розкладу",
    "",
    "Аркуш «Шаблон»: у рядку 2 — шифри груп (кожна група займає три колонки:",
    "Дисципліна | Викладач | Ауд.), далі по 16 рядків на день, по 2 рядки на пару.",
    "• Верхній рядок пари — чисельник, нижній — знаменник.",
    "• Нижній рядок порожній — у знаменнику та сама пара, що й у чисельнику.",
    "• Пари немає лише в одному тижні — напишіть у тому рядку «____».",
    "• Кілька викладачів в одній клітинці (Alt+Enter) — підгрупи; аудиторії — так само, у тому ж порядку.",
    "• Аудиторію можна не вказувати — її допишуть в адмінці.",
    "• Суботу не заповнюйте: вона йде за ротацією (перша — за понеділком, далі вівторок…).",
    "",
    "Аркуш «Викладачі» — ПІБ і пошта коледжу.",
    "Імпорт: адмінка → «Ще» → «Новий семестр» → «Імпорт готового розкладу».",
]


def _to_bytes(wb: Workbook) -> BytesIO:
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _instructions(wb: Workbook, lines: list[str]) -> None:
    ws = wb.create_sheet("Інструкція", 0)
    for i, line in enumerate(lines, 1):
        ws.cell(i, 1, line)
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 110


def _header(ws, titles: list[str], widths: list[int]) -> None:
    ws.append(titles)
    for i, w in enumerate(widths, 1):
        cell = ws.cell(1, i)
        cell.font, cell.fill = HEAD, HEAD_FILL
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def _teachers_sheet(wb: Workbook, teachers: list[tuple[str, str]]) -> None:
    ws = wb.create_sheet("Викладачі")
    _header(ws, ["Викладач", "Пошта"], [28, 36])
    for row in teachers:
        ws.append(list(row))


def load_template(example: bool = True) -> BytesIO:
    """Файл навантаження для генератора (з прикладом або порожній)."""
    wb = Workbook()
    wb.remove(wb.active)
    _instructions(wb, LOAD_INSTRUCTIONS)

    ws = wb.create_sheet("Навантаження")
    _header(ws, ["Група", "Дисципліна", "Викладач", "Кількість пар на тиждень", "Зміна"], [12, 44, 28, 14, 8])
    for row in EXAMPLE_LOAD if example else []:
        ws.append(list(row))
    for r in range(2, ws.max_row + 1):
        ws.cell(r, 3).alignment = WRAP
    pairs = DataValidation(type="decimal", operator="between", formula1="0.5", formula2="20", allow_blank=True,
                           errorTitle="Кількість пар", error="Число від 0,5 до 20, ціле або з половиною")
    shift = DataValidation(type="list", formula1='"1,2"', allow_blank=True,
                           errorTitle="Зміна", error="Зміна — 1 або 2")
    ws.add_data_validation(pairs)
    ws.add_data_validation(shift)
    pairs.add("D2:D2000")
    shift.add("E2:E2000")

    rooms = wb.create_sheet("аудиторії")
    _header(rooms, ["№", "Аудиторія", "Примітка"], [6, 14, 30])
    for i, (name, note) in enumerate(EXAMPLE_ROOMS if example else [], 1):
        rooms.append([i, name, note])

    _teachers_sheet(wb, [(t, "") for t in EXAMPLE_TEACHERS] if example else [])
    return _to_bytes(wb)


def grid_template(groups: list[str], entries=(), teachers: list[tuple[str, str]] = ()) -> BytesIO:
    """Сітка «Шаблон» для імпорту готового розкладу.

    entries — записи постійного розкладу (TimetableEntry), щоб вивантажити його
    для правок; без них сітка порожня.
    """
    wb = Workbook()
    wb.remove(wb.active)
    _instructions(wb, GRID_INSTRUCTIONS)
    ws = wb.create_sheet("Шаблон")
    ws["C1"] = "Верхній рядок пари — чисельник, нижній — знаменник (порожній = як чисельник)"
    ws["C1"].font = Font(italic=True)
    ws.cell(2, 1, "Група").font = HEAD
    ws.cell(3, 1, "День").font = HEAD
    ws.cell(3, 2, "Урок").font = HEAD
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 6

    for i, group in enumerate(groups):
        col = 3 + i * 3
        ws.cell(2, col, group)
        ws.merge_cells(start_row=2, start_column=col, end_row=2, end_column=col + 2)
        ws.cell(2, col).alignment, ws.cell(2, col).font, ws.cell(2, col).fill = CENTER, HEAD, HEAD_FILL
        for k, (title, width) in enumerate((("Дисципліна", 30), ("Викладач", 20), ("Ауд.", 7))):
            ws.cell(3, col + k, title).font = HEAD
            ws.column_dimensions[get_column_letter(col + k)].width = width

    last_col = 2 + len(groups) * 3
    for d, day in enumerate(DAYS):
        top = FIRST_ROW + d * ROWS_PER_DAY
        ws.cell(top, 1, day).alignment = Alignment(text_rotation=90, horizontal="center", vertical="center")
        ws.merge_cells(start_row=top, start_column=1, end_row=top + ROWS_PER_DAY - 1, end_column=1)
        for p in range(PAIRS_PER_DAY):
            row = top + p * 2
            ws.cell(row, 2, p + 1).alignment = CENTER
            ws.merge_cells(start_row=row, start_column=2, end_row=row + 1, end_column=2)
            for c in range(3, last_col + 1):
                ws.cell(row, c).border = Border(left=THIN, right=THIN, top=THIN, bottom=DOTTED)
                ws.cell(row + 1, c).border = Border(left=THIN, right=THIN, bottom=THIN)
                ws.cell(row, c).alignment = ws.cell(row + 1, c).alignment = WRAP
    ws.freeze_panes = "C4"

    by_key = {(e.week, e.day, e.pair, e.group): e for e in entries}
    for (week, day, pair, group), e in by_key.items():
        if group not in groups or week != 0 and _same(e, by_key.get((0, day, pair, group))):
            continue
        row = FIRST_ROW + day * ROWS_PER_DAY + (pair - 1) * 2 + week
        col = 3 + groups.index(group) * 3
        ws.cell(row, col, e.subject)
        ws.cell(row, col + 1, "\n".join(e.teachers))
        ws.cell(row, col + 2, "\n".join(e.rooms))
        other = (1 - week, day, pair, group)
        if week == 0 and other not in by_key:  # лише в чисельнику
            ws.cell(row + 1, col, "____")

    _teachers_sheet(wb, list(teachers))
    return _to_bytes(wb)


def _same(a, b) -> bool:
    return b is not None and (a.subject, a.teachers, a.rooms) == (b.subject, b.teachers, b.rooms)
