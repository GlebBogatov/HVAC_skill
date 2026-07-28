#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка листа ОВ наложением слоя на страницу архитектурного PDF.

Наложение, а не перечерчивание: петли садятся на реальные стены АР, а не на
реконструкцию геометрии. Расхождение исключено по построению — но только если
лист откалиброван честно, поэтому скрипт отказывается собирать лист при
невязке хуже порога.

    python3 sheet_pdf.py --ar AR.pdf --page 5 --calib calib.json \
                         --layer ov_cokol.json --loops loop_K1.json loop_K2.json \
                         --out ОВ-05.pdf

Форматы calib.json и layer.json — в references/ishodnye-dannye.md и chertezhi.md.
Требуется: reportlab, pypdf.
"""
from __future__ import annotations
import argparse
import io
import json
import os
import sys

MM = 72.0 / 25.4          # точки в миллиметре бумаги

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]

COLORS = {
    "red": (0.85, 0.10, 0.10), "blue": (0.10, 0.25, 0.80),
    "green": (0.05, 0.55, 0.20), "black": (0, 0, 0),
    "gray": (0.45, 0.45, 0.45), "magenta": (0.65, 0.15, 0.65),
    "cyan": (0.10, 0.60, 0.70), "yellow": (0.85, 0.65, 0.05),
}
LAYER_COLORS = {
    "TP-SUPPLY": "magenta", "HEAT-MAINS-T1": "red", "HEAT-MAINS-T2": "blue",
    "EQUIPMENT": "green", "RISER": "yellow", "SLEEVE": "cyan",
    "ROOMS": "gray", "AXES": "gray", "TEXT": "black", "DIM": "black",
}


# --------------------------------------------------------------------------- #
#  Калибровка
# --------------------------------------------------------------------------- #

class Transform:
    """Модельные миллиметры → точки страницы PDF."""

    def __init__(self, calib: dict):
        self.ax, self.bx = self._axis(calib["x"])
        self.ay, self.by = self._axis(calib["y"])

    @staticmethod
    def _axis(d):
        a = (float(d["pt2"]) - float(d["pt1"])) / (float(d["mm2"]) - float(d["mm1"]))
        b = float(d["pt1"]) - a * float(d["mm1"])
        return a, b

    def x(self, mm): return self.ax * float(mm) + self.bx
    def y(self, mm): return self.ay * float(mm) + self.by
    def p(self, pt): return (self.x(pt[0]), self.y(pt[1]))

    def scale(self, mm):
        """Длина в модельных мм → длина в точках (по модулю масштаба X)."""
        return abs(self.ax) * float(mm)


def check_calibration(tr: Transform, calib: dict, tol_pct: float):
    """Невязка по контрольным пролётам осей. Возвращает (отчёт, максимум %)."""
    rows = []
    worst = 0.0
    for c in calib.get("checks", []):
        axis = c["axis"]
        mm = float(c["mm"])
        got = tr.x(mm) if axis == "x" else tr.y(mm)
        exp = float(c["pt_expected"])
        span_pt = abs((tr.ax if axis == "x" else tr.ay) * mm) or 1.0
        dev_pt = got - exp
        dev_pct = abs(dev_pt) / span_pt * 100.0
        worst = max(worst, dev_pct)
        rows.append((axis, mm, exp, got, dev_pt, dev_pct))

    print("Калибровка листа:", file=sys.stderr)
    print("  масштаб X: %.6f pt/мм   Y: %.6f pt/мм" % (tr.ax, tr.ay), file=sys.stderr)
    d = abs(abs(tr.ax) - abs(tr.ay)) / max(abs(tr.ax), abs(tr.ay)) * 100.0
    print("  расхождение масштабов X/Y: %.3f %% — %s"
          % (d, "норма" if d < 0.5 else "НЕРАВНОМЕРНОЕ МАСШТАБИРОВАНИЕ"), file=sys.stderr)
    for axis, mm, exp, got, dev_pt, dev_pct in rows:
        print("  %s = %8.0f мм: ожидалось %8.2f pt, получено %8.2f pt, "
              "невязка %+6.2f pt (%.3f %%)" % (axis, mm, exp, got, dev_pt, dev_pct),
              file=sys.stderr)
    if rows:
        print("  максимальная невязка %.3f %% при допуске %.3f %% — %s"
              % (worst, tol_pct, "принято" if worst <= tol_pct else "ОТКАЗ"),
              file=sys.stderr)
    else:
        print("  контрольные точки не заданы — калибровка не проверена!", file=sys.stderr)
    return rows, worst


# --------------------------------------------------------------------------- #
#  Отрисовка
# --------------------------------------------------------------------------- #

def register_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont("OV", path))
            return "OV"
    print("ВНИМАНИЕ: шрифт с кириллицей не найден, текст будет нечитаем.", file=sys.stderr)
    return "Helvetica"


def set_color(c, name):
    r, g, b = COLORS.get(name, COLORS["black"])
    c.setStrokeColorRGB(r, g, b)
    c.setFillColorRGB(r, g, b)


def draw_layer(c, tr, data, font):
    for p in data.get("polylines", []):
        layer = p.get("layer", "TEXT")
        set_color(c, p.get("color") or LAYER_COLORS.get(layer, "red"))
        c.setLineWidth(float(p.get("width", 0.7)))
        c.setDash([3, 2] if p.get("style") == "dashed" else [])
        path = c.beginPath()
        pts = p["pts"]
        path.moveTo(*tr.p(pts[0]))
        for q in pts[1:]:
            path.lineTo(*tr.p(q))
        if p.get("closed"):
            path.close()
        c.drawPath(path, stroke=1, fill=0)
    c.setDash([])

    for l in data.get("lines", []):
        layer = l.get("layer", "TEXT")
        set_color(c, l.get("color") or LAYER_COLORS.get(layer, "black"))
        c.setLineWidth(float(l.get("width", 0.7)))
        c.line(*tr.p(l["p1"]), *tr.p(l["p2"]))

    for ci in data.get("circles", []):
        layer = ci.get("layer", "RISER")
        set_color(c, ci.get("color") or LAYER_COLORS.get(layer, "black"))
        c.setLineWidth(0.7)
        c.circle(tr.x(ci["c"][0]), tr.y(ci["c"][1]), tr.scale(ci["r"]),
                 stroke=1, fill=0)

    for r in data.get("rects", []):
        layer = r.get("layer", "EQUIPMENT")
        set_color(c, r.get("color") or LAYER_COLORS.get(layer, "green"))
        c.setLineWidth(float(r.get("width", 0.9)))
        x1, y1 = tr.p(r["p1"])
        x2, y2 = tr.p(r["p2"])
        c.rect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1), stroke=1, fill=0)

    for t in data.get("texts", []):
        set_color(c, t.get("color", "black"))
        size = max(4.0, tr.scale(float(t.get("h", 100))))
        c.setFont(font, size)
        c.saveState()
        x, y = tr.p(t["pt"])
        c.translate(x, y)
        if t.get("rot"):
            c.rotate(float(t["rot"]))
        c.drawString(0, 0, t["text"])
        c.restoreState()


def draw_table(c, table, x_mm, y_mm, font, w_mm=70.0, row_mm=5.0, size=7.0):
    """Ведомость. Координаты — в миллиметрах бумаги от левого нижнего угла листа."""
    x, y = x_mm * MM, y_mm * MM
    w, rh = w_mm * MM, row_mm * MM
    header = table.get("header", [])
    rows = table.get("rows", [])
    ncol = max(1, len(header))
    cw = [w * float(f) for f in table.get("widths", [1.0 / ncol] * ncol)]

    c.setFillColorRGB(1, 1, 1)
    c.setStrokeColorRGB(0, 0, 0)
    total_h = rh * (len(rows) + (2 if table.get("title") else 1))
    c.rect(x, y - total_h, w, total_h, stroke=1, fill=1)
    c.setFillColorRGB(0, 0, 0)

    cy = y
    if table.get("title"):
        c.setFont(font, size + 1)
        c.drawString(x + 2, cy - rh + 2, table["title"])
        cy -= rh
    c.setFont(font, size)
    for line in ([header] if header else []) + rows:
        cx = x
        for i, cell in enumerate(line):
            c.drawString(cx + 2, cy - rh + 2, str(cell))
            cx += cw[i] if i < len(cw) else w / ncol
        cy -= rh
        c.setLineWidth(0.3)
        c.line(x, cy, x + w, cy)


def fit_text(c, font, text, max_w, size, min_size=4.0):
    """Подобрать кегль, чтобы строка влезла в отведённую ширину, иначе обрезать."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    s = size
    while s > min_size and stringWidth(text, font, s) > max_w:
        s -= 0.5
    if stringWidth(text, font, s) > max_w:
        while text and stringWidth(text + "…", font, s) > max_w:
            text = text[:-1]
        text += "…"
    return text, s


def draw_frame(c, page_w, page_h, left=20.0, other=5.0):
    """
    Рамка листа по ГОСТ Р 21.101-2020 / ГОСТ 2.301-68: сплошная основная линия
    с полем 20 мм слева (подшивка) и по 5 мм сверху, справа, снизу.
    Нужна для самостоятельных листов; при наложении на подложку АР рамка обычно
    уже есть — тогда эту функцию не вызывать (флаг --frame выключен по умолчанию).
    """
    x = left * MM
    y = other * MM
    w = page_w - (left + other) * MM
    h = page_h - 2 * other * MM
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(1.0)                       # сплошная толстая основная, ГОСТ 2.303
    c.rect(x, y, w, h, stroke=1, fill=0)


def draw_stamp(c, stamp, page_w, page_h, font):
    """Основная надпись, форма 3, 185×55 мм в правом нижнем углу
    (ГОСТ Р 21.101-2020). Примыкает к внутренней рамке листа."""
    w, h = 185 * MM, 55 * MM
    m = 5 * MM
    x, y = page_w - w - m, m

    c.setFillColorRGB(1, 1, 1)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(1.0)
    c.rect(x, y, w, h, stroke=1, fill=1)
    # Сброс заливки обязателен: после белой плашки текст рисуется белым по белому,
    # и в структуре PDF это не видно — ловится только растром.
    c.setFillColorRGB(0, 0, 0)

    cx1 = x + w * 0.34          # граница «подписи | наименование»
    cx2 = x + w * 0.74          # граница «наименование | шифр»
    c.setLineWidth(0.4)
    c.line(cx1, y, cx1, y + h)
    c.line(cx2, y, cx2, y + h)
    for fr in (1 / 3.0, 2 / 3.0):
        c.line(x, y + h * fr, cx1, y + h * fr)
    c.line(cx2, y + h * 0.62, x + w, y + h * 0.62)
    c.line(cx2, y + h * 0.32, x + w, y + h * 0.32)

    # левая колонка: подписи
    rows = [("Разработал", stamp.get("designer", ""), stamp.get("date", "")),
            ("Проверил", stamp.get("checker", ""), ""),
            ("ГИП", stamp.get("gip", ""), "")]
    for i, (role, who, date) in enumerate(rows):
        ry = y + h * (3 - i) / 3.0
        c.setFont(font, 5.5)
        c.drawString(x + 2, ry - 8, role)
        c.setFont(font, 6.5)
        c.drawString(x + w * 0.11, ry - 8, who)
        if date:
            c.drawString(x + w * 0.24, ry - 8, date)

    # центральная колонка: объект и наименование листа
    col_w = cx2 - cx1 - 6
    t, sz = fit_text(c, font, stamp.get("object", ""), col_w, 8.5)
    c.setFont(font, sz)
    c.drawString(cx1 + 3, y + h - 12, t)
    t, sz = fit_text(c, font, stamp.get("sheet_name", ""), col_w, 10.0)
    c.setFont(font, sz)
    c.drawString(cx1 + 3, y + h * 0.42, t)

    # правая колонка: шифр, стадия, листы, масштаб
    rcol = x + w - cx2 - 6
    t, sz = fit_text(c, font, stamp.get("code", ""), rcol, 8.5)
    c.setFont(font, sz)
    c.drawString(cx2 + 3, y + h - 12, t)
    c.setFont(font, 7)
    c.drawString(cx2 + 3, y + h * 0.62 - 10, "Стадия  %s" % stamp.get("stage", "Р"))
    c.drawString(cx2 + 3, y + h * 0.32 - 10,
                 "Лист %s из %s" % (stamp.get("sheet_no", ""), stamp.get("sheets", "")))
    c.drawString(cx2 + 3, y + 5, stamp.get("scale", "М 1:100"))


def draw_legend(c, items, x_mm, y_mm, font, size=7.0, row_mm=5.0):
    x, y = x_mm * MM, y_mm * MM
    c.setFont(font, size + 1)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(x, y, "Условные обозначения")
    y -= row_mm * MM
    c.setFont(font, size)
    for it in items:
        set_color(c, it.get("color", "red"))
        c.setLineWidth(float(it.get("width", 1.0)))
        c.setDash([3, 2] if it.get("style") == "dashed" else [])
        c.line(x, y + 2, x + 12 * MM, y + 2)
        c.setDash([])
        c.setFillColorRGB(0, 0, 0)
        c.drawString(x + 14 * MM, y, it.get("text", ""))
        y -= row_mm * MM


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Лист ОВ на подложке АР")
    ap.add_argument("--ar", required=True, help="архитектурный PDF (подложка)")
    ap.add_argument("--page", type=int, required=True, help="номер страницы АР, с 1")
    ap.add_argument("--calib", required=True, help="calib.json")
    ap.add_argument("--layer", help="layer.json со слоем ОВ")
    ap.add_argument("--loops", nargs="*", default=[], help="JSON петель от spiral.py")
    ap.add_argument("--out", required=True, help="выходной PDF")
    ap.add_argument("--tol", type=float, default=0.2,
                    help="допустимая невязка калибровки, %% (по умолчанию 0,2)")
    ap.add_argument("--force", action="store_true",
                    help="собрать лист несмотря на невязку (только для черновика)")
    ap.add_argument("--cover-ar-stamp", nargs=4, type=float,
                    metavar=("X", "Y", "W", "H"),
                    help="закрыть штамп АР белым прямоугольником, мм от левого нижнего угла")
    ap.add_argument("--table-at", nargs=2, type=float, default=[15.0, 200.0],
                    metavar=("X", "Y"), help="положение ведомости, мм")
    ap.add_argument("--legend-at", nargs=2, type=float, default=[15.0, 120.0],
                    metavar=("X", "Y"), help="положение легенды, мм")
    ap.add_argument("--frame", action="store_true",
                    help="нарисовать рамку по ГОСТ (поле 20 мм слева, 5 мм с трёх "
                         "сторон) — для самостоятельных листов; на подложке АР рамка "
                         "обычно уже есть, тогда флаг не нужен")
    args = ap.parse_args()

    from reportlab.pdfgen import canvas as rl_canvas
    from pypdf import PdfReader, PdfWriter

    with open(args.calib, encoding="utf-8") as f:
        calib = json.load(f)
    tr = Transform(calib)
    _, worst = check_calibration(tr, calib, args.tol)
    if worst > args.tol and not args.force:
        print("\nЛист не собран: невязка калибровки %.3f %% превышает допуск %.3f %%.\n"
              "Проверьте марки осей или перечертите план — накладывать нельзя.\n"
              "Для черновика используйте --force." % (worst, args.tol), file=sys.stderr)
        sys.exit(2)

    reader = PdfReader(args.ar)
    base = reader.pages[args.page - 1]
    pw = float(base.mediabox.width)
    ph = float(base.mediabox.height)
    print("  страница %d: %.1f × %.1f pt (%.0f × %.0f мм)"
          % (args.page, pw, ph, pw / MM, ph / MM), file=sys.stderr)

    data = {}
    if args.layer:
        with open(args.layer, encoding="utf-8") as f:
            data = json.load(f)
    for path in args.loops:
        with open(path, encoding="utf-8") as f:
            loop = json.load(f)
        data.setdefault("polylines", []).append({
            "layer": loop.get("layer", "TP-" + loop.get("name", "X")),
            "pts": loop["pts"], "color": "red", "width": 0.8})

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(pw, ph))
    font = register_font()

    if args.cover_ar_stamp:
        x, y, w, h = [v * MM for v in args.cover_ar_stamp]
        c.setFillColorRGB(1, 1, 1)
        c.setStrokeColorRGB(1, 1, 1)
        c.rect(x, y, w, h, stroke=1, fill=1)
        c.setFillColorRGB(0, 0, 0)          # обязательный сброс

    draw_layer(c, tr, data, font)

    if args.frame:
        draw_frame(c, pw, ph)

    if data.get("table"):
        draw_table(c, data["table"], args.table_at[0], args.table_at[1], font)
    if data.get("legend"):
        draw_legend(c, data["legend"], args.legend_at[0], args.legend_at[1], font)
    if data.get("stamp"):
        draw_stamp(c, data["stamp"], pw, ph, font)

    c.showPage()
    c.save()
    buf.seek(0)

    overlay = PdfReader(buf).pages[0]
    base.merge_page(overlay)
    writer = PdfWriter()
    writer.add_page(base)
    with open(args.out, "wb") as f:
        writer.write(f)

    print("\nЛист собран: %s" % args.out, file=sys.stderr)
    print("Обязательно растрируйте и посмотрите глазами:", file=sys.stderr)
    print("  pdftoppm -r 120 -png %s check" % args.out, file=sys.stderr)
    print("Проверка структуры PDF ошибок отрисовки не ловит.", file=sys.stderr)


if __name__ == "__main__":
    main()
