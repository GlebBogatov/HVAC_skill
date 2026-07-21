#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Экспорт слоя ОВ в DXF R12 — без внешних зависимостей.

Формат R12 выбран сознательно: открывается всем, от AutoCAD и nanoCAD до
LibreCAD, и не требует ezdxf в окружении. Единицы — миллиметры.

    python3 dxf_export.py --layer ov_cokol.json --out ОВ-05.dxf
    python3 dxf_export.py --loops loop_K1.json loop_K2.json --out petli.dxf

Формат layer.json описан в references/chertezhi.md.
Файлы петель от spiral.py подхватываются напрямую через --loops.
"""
from __future__ import annotations
import argparse
import json
import sys

# ACI-цвета по таблице слоёв (references/chertezhi.md)
LAYER_COLORS = {
    "TP-SUPPLY": 6, "HEAT-MAINS-T1": 1, "HEAT-MAINS-T2": 5,
    "EQUIPMENT": 3, "RISER": 2, "SLEEVE": 4, "ROOMS": 8,
    "AXES": 8, "TEXT": 7, "DIM": 7,
}
COLOR_NAMES = {"red": 1, "yellow": 2, "green": 3, "cyan": 4,
               "blue": 5, "magenta": 6, "white": 7, "gray": 8}


def layer_color(name: str, explicit=None) -> int:
    if explicit is not None:
        if isinstance(explicit, str):
            return COLOR_NAMES.get(explicit, 7)
        return int(explicit)
    if name in LAYER_COLORS:
        return LAYER_COLORS[name]
    if name.startswith("TP-"):
        return 1                       # петли тёплого пола — красные
    return 7


class DXF:
    def __init__(self):
        self.layers = {}
        self.ents = []

    def _layer(self, name, color=None, linetype="CONTINUOUS"):
        if name not in self.layers:
            self.layers[name] = (layer_color(name, color), linetype)
        return name

    def polyline(self, layer, pts, color=None, closed=False, linetype="CONTINUOUS"):
        if len(pts) < 2:
            return
        self._layer(layer, color, linetype)
        e = ["0\nPOLYLINE\n8\n%s\n66\n1\n70\n%d\n10\n0.0\n20\n0.0\n30\n0.0"
             % (layer, 1 if closed else 0)]
        for x, y in pts:
            e.append("0\nVERTEX\n8\n%s\n10\n%.3f\n20\n%.3f\n30\n0.0" % (layer, x, y))
        e.append("0\nSEQEND\n8\n%s" % layer)
        self.ents.append("\n".join(e))

    def line(self, layer, p1, p2, color=None):
        self._layer(layer, color)
        self.ents.append("0\nLINE\n8\n%s\n10\n%.3f\n20\n%.3f\n30\n0.0\n11\n%.3f\n21\n%.3f\n31\n0.0"
                         % (layer, p1[0], p1[1], p2[0], p2[1]))

    def circle(self, layer, c, r, color=None):
        self._layer(layer, color)
        self.ents.append("0\nCIRCLE\n8\n%s\n10\n%.3f\n20\n%.3f\n30\n0.0\n40\n%.3f"
                         % (layer, c[0], c[1], r))

    def text(self, layer, pt, s, h=100.0, color=None, rot=0.0):
        self._layer(layer, color)
        self.ents.append("0\nTEXT\n8\n%s\n10\n%.3f\n20\n%.3f\n30\n0.0\n40\n%.3f\n1\n%s\n50\n%.2f"
                         % (layer, pt[0], pt[1], h, s, rot))

    def rect(self, layer, p1, p2, color=None):
        x1, y1 = p1
        x2, y2 = p2
        self.polyline(layer, [(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
                      color=color, closed=True)

    def dumps(self, codepage="ANSI_1251") -> str:
        out = []
        out.append("0\nSECTION\n2\nHEADER")
        out.append("9\n$ACADVER\n1\nAC1009")
        out.append("9\n$DWGCODEPAGE\n3\n%s" % codepage)
        out.append("9\n$INSUNITS\n70\n4")            # 4 = миллиметры
        out.append("0\nENDSEC")

        out.append("0\nSECTION\n2\nTABLES")
        out.append("0\nTABLE\n2\nLTYPE\n70\n2")
        out.append("0\nLTYPE\n2\nCONTINUOUS\n70\n0\n3\nSolid line\n72\n65\n73\n0\n40\n0.0")
        out.append("0\nLTYPE\n2\nDASHED\n70\n0\n3\n__ __ __\n72\n65\n73\n2\n40\n15.0\n49\n10.0\n49\n-5.0")
        out.append("0\nENDTAB")
        out.append("0\nTABLE\n2\nLAYER\n70\n%d" % max(1, len(self.layers)))
        for name, (color, lt) in self.layers.items():
            out.append("0\nLAYER\n2\n%s\n70\n0\n62\n%d\n6\n%s" % (name, color, lt))
        out.append("0\nENDTAB")
        out.append("0\nENDSEC")

        out.append("0\nSECTION\n2\nENTITIES")
        out.extend(self.ents)
        out.append("0\nENDSEC")
        out.append("0\nEOF")
        return "\n".join(out) + "\n"


def add_layer_json(dxf: DXF, data: dict):
    for p in data.get("polylines", []):
        lt = "DASHED" if p.get("style") == "dashed" else "CONTINUOUS"
        dxf.polyline(p.get("layer", "TEXT"), p["pts"], color=p.get("color"),
                     closed=p.get("closed", False), linetype=lt)
    for l in data.get("lines", []):
        dxf.line(l.get("layer", "TEXT"), l["p1"], l["p2"], color=l.get("color"))
    for c in data.get("circles", []):
        dxf.circle(c.get("layer", "RISER"), c["c"], c["r"], color=c.get("color"))
    for r in data.get("rects", []):
        dxf.rect(r.get("layer", "EQUIPMENT"), r["p1"], r["p2"], color=r.get("color"))
    for t in data.get("texts", []):
        dxf.text(t.get("layer", "TEXT"), t["pt"], t["text"],
                 h=float(t.get("h", 100)), color=t.get("color"),
                 rot=float(t.get("rot", 0)))


def main():
    ap = argparse.ArgumentParser(description="Экспорт слоя ОВ в DXF R12")
    ap.add_argument("--layer", help="layer.json со слоем ОВ")
    ap.add_argument("--loops", nargs="*", default=[],
                    help="JSON-файлы петель от spiral.py")
    ap.add_argument("--out", required=True, help="выходной DXF")
    ap.add_argument("--encoding", default="cp1251",
                    help="кодировка файла (cp1251 для российских САПР, utf-8 для современных)")
    ap.add_argument("--label-loops", action="store_true",
                    help="подписать петли маркой и длиной")
    args = ap.parse_args()

    if not args.layer and not args.loops:
        ap.error("нужен --layer или --loops")

    dxf = DXF()

    if args.layer:
        with open(args.layer, encoding="utf-8") as f:
            add_layer_json(dxf, json.load(f))

    for path in args.loops:
        with open(path, encoding="utf-8") as f:
            loop = json.load(f)
        dxf.polyline(loop.get("layer", "TP-" + loop.get("name", "X")), loop["pts"])
        if args.label_loops:
            pts = loop["pts"]
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            dxf.text("TEXT", (cx, cy), "%s  %.1f м"
                     % (loop.get("name", ""), loop.get("length_total_m", 0)), h=120)

    body = dxf.dumps("ANSI_1251" if args.encoding.lower() in ("cp1251", "windows-1251") else "ANSI_1252")
    with open(args.out, "w", encoding=args.encoding, errors="replace") as f:
        f.write(body)

    print("DXF записан: %s" % args.out, file=sys.stderr)
    print("  слоёв: %d — %s" % (len(dxf.layers), ", ".join(sorted(dxf.layers))), file=sys.stderr)
    print("  объектов: %d" % len(dxf.ents), file=sys.stderr)
    print("  единицы: мм, кодировка: %s" % args.encoding, file=sys.stderr)


if __name__ == "__main__":
    main()
