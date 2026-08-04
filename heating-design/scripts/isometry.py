#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Аксонометрическая (изометрическая) схема системы отопления.

Косоугольная фронтальная изометрия по ГОСТ 2.317-2011 (ось Y под 45°), состав
схемы по ГОСТ 21.602-2016. Проецирует 3D-схему (узлы в мм: x вдоль фронта,
y в глубину, z вверх) в 2D и выдаёт:

  * SVG для быстрого просмотра         (--svg out.svg)
  * layer.json для dxf_export/sheet_pdf (--layer out.json)

    python3 isometry.py shema.json --svg ОВ-09.svg --layer ОВ-09.json
    python3 dxf_export.py --layer ОВ-09.json --out ОВ-09.dxf

Формат входа и методика — references/izometriya.md. Зависимостей нет.
"""
from __future__ import annotations
import argparse
import json
import math
import sys

SYS_COLOR = {"T1": "red", "T2": "blue"}          # подача / обратка
SVG_COLOR = {"red": "#c81414", "blue": "#1a3fcc", "green": "#0e8c33",
             "black": "#000000", "gray": "#707070", "magenta": "#a626a6"}


# --------------------------------------------------------------------------- #
#  Проекция
# --------------------------------------------------------------------------- #

def projector(k, angle_deg=45.0):
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)

    def proj(p):
        x, y, z = p
        return (x + k * y * ca, z + k * y * sa)     # (Xэкр, Yэкр вверх)
    return proj


# --------------------------------------------------------------------------- #
#  Глифы элементов (в плоскости проекции, мм)
# --------------------------------------------------------------------------- #

def glyph(kind, X, Y, label=""):
    """Возвращает список примитивов layer.json для элемента в точке (X, Y)."""
    L = "EQUIPMENT"
    T = "TEXT"
    prim = {"polylines": [], "lines": [], "circles": [], "rects": [], "texts": []}

    def rect(x1, y1, x2, y2, color="green", w=0.6):
        prim["rects"].append({"layer": L, "p1": [x1, y1], "p2": [x2, y2],
                              "color": color, "width": w})

    def line(x1, y1, x2, y2, color="green", w=0.6):
        prim["lines"].append({"layer": L, "p1": [x1, y1], "p2": [x2, y2],
                             "color": color, "width": w})

    def circ(cx, cy, r, color="green"):
        prim["circles"].append({"layer": L, "c": [cx, cy], "r": r, "color": color})

    def text(x, y, s, h=120, color="black"):
        if s:
            prim["texts"].append({"layer": T, "pt": [x, y], "text": s, "h": h,
                                 "color": color})

    if kind == "radiator":
        w, h = 600, 220
        rect(X - w / 2, Y - h / 2, X + w / 2, Y + h / 2)
        for i in range(1, 5):
            x = X - w / 2 + i * w / 5
            line(x, Y - h / 2, x, Y + h / 2)
        text(X - w / 2, Y + h / 2 + 60, label)

    elif kind == "convector":
        w, h = 600, 160
        rect(X - w / 2, Y - h / 2, X + w / 2, Y + h / 2)
        for i in range(1, 8):
            x = X - w / 2 + i * w / 8
            line(x, Y - h / 2, x, Y + h / 2)
        text(X - w / 2, Y + h / 2 + 60, label)

    elif kind == "boiler":
        w, h = 700, 900
        rect(X - w / 2, Y, X + w / 2, Y + h)
        circ(X, Y + h * 0.35, 180)
        text(X - w / 2, Y - 80, label)

    elif kind == "valve":                       # запорная — бабочка
        s = 150
        prim["polylines"].append({"layer": L, "pts": [
            [X - s, Y - s], [X + s, Y + s], [X + s, Y - s],
            [X - s, Y + s], [X - s, Y - s]], "color": "green", "width": 0.7})
        text(X + s + 40, Y + s, label)

    elif kind == "pump":                        # насос — круг со стрелкой
        r = 180
        circ(X, Y, r)
        line(X - r, Y, X + r, Y)
        line(X + r * 0.4, Y + r * 0.4, X + r, Y)
        line(X + r * 0.4, Y - r * 0.4, X + r, Y)
        text(X + r + 40, Y, label)

    elif kind == "collector":                   # гребёнка
        w, h = 900, 160
        rect(X - w / 2, Y - h / 2, X + w / 2, Y + h / 2)
        for i in range(1, 6):
            x = X - w / 2 + i * w / 6
            line(x, Y - h / 2, x, Y - h / 2 - 140)
        text(X - w / 2, Y + h / 2 + 60, label)

    elif kind == "riser":                       # маркер стояка
        circ(X, Y, 130, "black")
        text(X - 60, Y - 40, label, h=110)

    elif kind == "airvent":                     # воздухоотводчик
        circ(X, Y, 90, "green")
        line(X, Y - 90, X, Y - 260)
        text(X + 120, Y - 200, label or "возд.", h=100)

    elif kind == "drain":                       # спускник
        s = 120
        prim["polylines"].append({"layer": L, "pts": [
            [X - s, Y + s], [X + s, Y + s], [X, Y - s], [X - s, Y + s]],
            "color": "green", "width": 0.7})
        text(X + s + 40, Y + s, label or "спуск", h=100)

    elif kind == "expansion":                   # расширительный бак
        circ(X, Y, 220, "green")
        text(X + 260, Y, label)

    else:
        circ(X, Y, 120, "green")
        text(X + 160, Y, label)

    return prim


def merge(dst, src):
    for key in ("polylines", "lines", "circles", "rects", "texts"):
        dst.setdefault(key, []).extend(src.get(key, []))


# --------------------------------------------------------------------------- #
#  Построение схемы
# --------------------------------------------------------------------------- #

def build(data):
    k = float(data.get("k_depth", 1.0))
    proj = projector(k, float(data.get("angle", 45.0)))
    nodes = {name: proj(p) for name, p in data["nodes"].items()}

    layer = {"title": data.get("title", "Схема системы отопления"),
             "sheet": data.get("sheet", ""),
             "polylines": [], "lines": [], "circles": [], "rects": [], "texts": []}

    # трубы
    for pipe in data.get("pipes", []):
        a = nodes[pipe["from"]]
        b = nodes[pipe["to"]]
        color = SYS_COLOR.get(pipe.get("sys", "T1"), "black")
        lay = "HEAT-MAINS-" + pipe.get("sys", "T1")
        layer["polylines"].append({"layer": lay, "pts": [list(a), list(b)],
                                   "color": color, "width": 1.0})
        # диаметр участка (Т1 подпись выше линии, Т2 ниже — чтобы не наложились)
        dn = pipe.get("dn")
        if dn:
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            off = 60 if pipe.get("sys", "T1") == "T1" else -180
            layer["texts"].append({"layer": "TEXT", "pt": [mx + 40, my + off],
                                  "text": "Ø%s" % dn, "h": 100, "color": color})
        # номер стояка
        if pipe.get("riser"):
            layer["texts"].append({"layer": "TEXT", "pt": [a[0] - 260, (a[1] + b[1]) / 2],
                                  "text": pipe["riser"], "h": 120, "color": "black"})

    # элементы
    for el in data.get("elements", []):
        X, Y = nodes[el["at"]]
        merge(layer, glyph(el.get("type", ""), X, Y, el.get("label", "")))

    # свободные подписи
    for lb in data.get("labels", []):
        X, Y = nodes[lb["at"]]
        layer["texts"].append({"layer": "TEXT",
                              "pt": [X + float(lb.get("dx", 0)), Y + float(lb.get("dy", 0))],
                              "text": lb["text"], "h": float(lb.get("h", 100)),
                              "color": lb.get("color", "black")})
    return layer


# --------------------------------------------------------------------------- #
#  SVG
# --------------------------------------------------------------------------- #

def bounds(layer):
    xs, ys = [], []
    for p in layer["polylines"]:
        for x, y in p["pts"]:
            xs.append(x); ys.append(y)
    for l in layer["lines"]:
        xs += [l["p1"][0], l["p2"][0]]; ys += [l["p1"][1], l["p2"][1]]
    for c in layer["circles"]:
        xs += [c["c"][0] - c["r"], c["c"][0] + c["r"]]
        ys += [c["c"][1] - c["r"], c["c"][1] + c["r"]]
    for r in layer["rects"]:
        xs += [r["p1"][0], r["p2"][0]]; ys += [r["p1"][1], r["p2"][1]]
    for t in layer["texts"]:
        xs.append(t["pt"][0]); ys.append(t["pt"][1])
    if not xs:
        return 0, 0, 1000, 1000
    return min(xs), min(ys), max(xs), max(ys)


def to_svg(layer, scale=0.05, margin=60):
    x0, y0, x1, y1 = bounds(layer)
    W = (x1 - x0) * scale + 2 * margin
    H = (y1 - y0) * scale + 2 * margin

    def sx(x):
        return (x - x0) * scale + margin

    def sy(y):
        return H - ((y - y0) * scale + margin)      # flip Y (SVG вниз)

    def col(c):
        return SVG_COLOR.get(c, "#000000")

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" '
           'viewBox="0 0 %.0f %.0f">' % (W, H, W, H)]
    out.append('<rect width="%.0f" height="%.0f" fill="white"/>' % (W, H))

    for p in layer["polylines"]:
        pts = " ".join("%.1f,%.1f" % (sx(x), sy(y)) for x, y in p["pts"])
        out.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%.1f"/>'
                   % (pts, col(p.get("color", "black")), p.get("width", 1.0) * 1.4))
    for l in layer["lines"]:
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%.1f"/>'
                   % (sx(l["p1"][0]), sy(l["p1"][1]), sx(l["p2"][0]), sy(l["p2"][1]),
                      col(l.get("color", "black")), l.get("width", 0.6) * 1.4))
    for c in layer["circles"]:
        out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="%s" stroke-width="1"/>'
                   % (sx(c["c"][0]), sy(c["c"][1]), c["r"] * scale, col(c.get("color", "black"))))
    for r in layer["rects"]:
        x = min(sx(r["p1"][0]), sx(r["p2"][0]))
        y = min(sy(r["p1"][1]), sy(r["p2"][1]))
        w = abs(r["p2"][0] - r["p1"][0]) * scale
        h = abs(r["p2"][1] - r["p1"][1]) * scale
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" '
                   'stroke="%s" stroke-width="1"/>' % (x, y, w, h, col(r.get("color", "black"))))
    for t in layer["texts"]:
        fs = max(7, t.get("h", 100) * scale)
        out.append('<text x="%.1f" y="%.1f" font-family="Arial" font-size="%.1f" fill="%s">%s</text>'
                   % (sx(t["pt"][0]), sy(t["pt"][1]) - 2, fs, col(t.get("color", "black")),
                      _esc(t["text"])))
    # заголовок
    out.append('<text x="%.0f" y="%.0f" font-family="Arial" font-size="16" fill="#000">%s</text>'
               % (margin, 24, _esc(layer.get("title", ""))))
    out.append("</svg>")
    return "\n".join(out)


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Изометрическая схема отопления (ГОСТ 2.317 / 21.602)")
    ap.add_argument("input", help="JSON со схемой (узлы, трубы, элементы)")
    ap.add_argument("--svg", help="файл SVG для просмотра")
    ap.add_argument("--layer", help="layer.json для dxf_export/sheet_pdf")
    ap.add_argument("--scale", type=float, default=0.05, help="масштаб SVG, px/мм (по умолч. 0,05)")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    layer = build(data)

    if args.layer:
        with open(args.layer, "w", encoding="utf-8") as f:
            json.dump(layer, f, ensure_ascii=False, indent=2)
        print("layer.json записан: %s" % args.layer, file=sys.stderr)
    if args.svg:
        with open(args.svg, "w", encoding="utf-8") as f:
            f.write(to_svg(layer, args.scale))
        print("SVG записан: %s" % args.svg, file=sys.stderr)
    if not args.svg and not args.layer:
        print(to_svg(layer, args.scale))

    n_pipes = len(data.get("pipes", []))
    n_el = len(data.get("elements", []))
    print("  узлов %d, труб %d, элементов %d"
          % (len(data.get("nodes", {})), n_pipes, n_el), file=sys.stderr)


if __name__ == "__main__":
    main()
