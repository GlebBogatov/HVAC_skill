#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единый проектный файл: один источник данных для всех расчётов.

project.json держит объект, климат, конструкции, помещения, напольное отопление
(коллекторы и петли) и схему. Скрипт валидирует файл, сквозно проверяет
согласованность и либо эмитит вход для конкретного инструмента, либо прогоняет
весь конвейер. Мощности петель ТП берутся из ПОСЧИТАННЫХ теплопотерь, а не
вводятся повторно — в этом смысл единого источника.

    python3 project.py project.json --check
    python3 project.py project.json --emit heatloss > hl.json
    python3 project.py project.json --emit hydro    > hy.json
    python3 project.py project.json --emit isometry > iso.json
    python3 project.py project.json --run --outdir out/

Формат файла — references/proekt-fajl.md. Существующие скрипты (heatloss, hydro,
isometry) не переписываются: project.py извлекает из единого файла нужный срез
и вызывает их как библиотеки.
"""
from __future__ import annotations
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import heatloss                                   # noqa: E402
import hydro                                      # noqa: E402
import isometry                                   # noqa: E402


# --------------------------------------------------------------------------- #
#  Извлечение срезов из единого файла
# --------------------------------------------------------------------------- #

def heatloss_data(prj):
    return {
        "project": prj.get("meta", {}).get("object", ""),
        "climate": prj["climate"],
        "building_group": prj.get("building_group", 2),
        "constructions": prj["constructions"],
        "rooms": prj["rooms"],
    }


def room_loads(prj):
    """{имя помещения: Q, Вт} из расчёта теплопотерь."""
    res = heatloss.calculate(heatloss_data(prj))
    return {r["name"]: r["Q"] for r in res["rooms"]}, res


def resolve_loop_Q(loops, loads):
    """Мощность каждой петли: явная Q_W, доля share от помещения либо поровну."""
    # сколько петель ссылается на каждое помещение (для деления поровну)
    per_room = {}
    for lp in loops:
        if "Q_W" not in lp and lp.get("room"):
            per_room[lp["room"]] = per_room.get(lp["room"], 0) + 1
    out = []
    for lp in loops:
        d = dict(lp)
        if "Q_W" in lp:
            pass
        elif lp.get("room") in loads:
            q = loads[lp["room"]]
            if "share" in lp:
                d["Q_W"] = round(q * float(lp["share"]))
            else:
                d["Q_W"] = round(q / per_room[lp["room"]])
        else:
            d["Q_W"] = 0
        out.append(d)
    return out


def hydro_data(prj):
    loads, _ = room_loads(prj)
    colls = []
    for c in prj.get("tp", {}).get("collectors", []):
        c2 = dict(c)
        c2["loops"] = resolve_loop_Q(c.get("loops", []), loads)
        colls.append(c2)
    return {"project": prj.get("meta", {}).get("object", ""), "collectors": colls}


def isometry_data(prj):
    sch = dict(prj.get("schema", {}))
    sch.setdefault("title", "Схема системы отопления")
    sch.setdefault("sheet", "ОВ-09")
    return sch


# --------------------------------------------------------------------------- #
#  Сквозная проверка согласованности
# --------------------------------------------------------------------------- #

def check(prj):
    issues = []
    ok = []

    meta = prj.get("meta", {})
    if not meta.get("object"):
        issues.append("meta.object не задан")
    if not meta.get("code"):
        issues.append("meta.code (шифр) не задан")

    # конструкции, на которые ссылаются помещения
    constr = set(prj.get("constructions", {}))
    used = set()
    for r in prj.get("rooms", []):
        for e in r.get("envelopes", []):
            used.add(e["constr"])
    missing = used - constr
    if missing:
        issues.append("конструкции не описаны: %s" % ", ".join(sorted(missing)))
    else:
        ok.append("все конструкции помещений описаны (%d)" % len(used))

    # теплопотери считаются
    try:
        loads, res = room_loads(prj)
        ok.append("теплопотери: %d помещений, Q = %.2f кВт"
                  % (len(loads), res["totals"]["Q"] / 1000.0))
        bal = res["checks"]["balance"]
        ok.append("баланс сходится (%d Вт)" % bal["dev"] if bal["ok"]
                  else "!! баланс НЕ сходится (%d Вт)" % bal["dev"])
    except Exception as e:                        # noqa: BLE001
        issues.append("расчёт теплопотерь падает: %s" % e)
        loads = {}

    # петли ТП
    marks = {}
    for c in prj.get("tp", {}).get("collectors", []):
        for lp in c.get("loops", []):
            marks[lp.get("name", "?")] = marks.get(lp.get("name", "?"), 0) + 1
            room = lp.get("room")
            if room and loads and room not in loads:
                issues.append("петля %s ссылается на помещение «%s», которого нет"
                              % (lp.get("name", "?"), room))
    dups = [m for m, n in marks.items() if n > 1]
    if dups:
        issues.append("марки петель повторяются: %s" % ", ".join(dups))
    elif marks:
        ok.append("марки петель уникальны (%d)" % len(marks))

    # покрытие мощности помещения петлями (если заданы share)
    if loads:
        by_room = {}
        for c in prj.get("tp", {}).get("collectors", []):
            for lp in resolve_loop_Q(c.get("loops", []), loads):
                if lp.get("room"):
                    by_room.setdefault(lp["room"], 0)
                    by_room[lp["room"]] += lp.get("Q_W", 0)
        for room, q in by_room.items():
            full = loads.get(room, 0)
            if full > 0:
                dev = (q - full) / full * 100
                if abs(dev) > 5:
                    issues.append("петли помещения «%s» покрывают %.0f Вт против %.0f Вт "
                                  "(%.0f %%) — проверить доли/догрев" % (room, q, full, dev))

    # схема: узлы труб и элементов существуют
    sch = prj.get("schema", {})
    nodes = set(sch.get("nodes", {}))
    for p in sch.get("pipes", []):
        for end in ("from", "to"):
            if p.get(end) not in nodes:
                issues.append("схема: труба ссылается на узел «%s», которого нет" % p.get(end))
    for el in sch.get("elements", []):
        if el.get("at") not in nodes:
            issues.append("схема: элемент «%s» в несуществующем узле «%s»"
                          % (el.get("type"), el.get("at")))
    if sch:
        ok.append("схема: %d узлов, %d труб, %d элементов"
                  % (len(nodes), len(sch.get("pipes", [])), len(sch.get("elements", []))))

    return ok, issues


# --------------------------------------------------------------------------- #

def run_all(prj, outdir):
    os.makedirs(outdir, exist_ok=True)
    written = []

    hl = heatloss.calculate(heatloss_data(prj))
    p = os.path.join(outdir, "teplopoteri.md")
    open(p, "w", encoding="utf-8").write(heatloss.to_markdown(hl))
    written.append(p)

    if prj.get("tp", {}).get("collectors"):
        p = os.path.join(outdir, "gidravlika.md")
        open(p, "w", encoding="utf-8").write(hydro.to_markdown(hydro_data(prj)))
        written.append(p)

    if prj.get("schema", {}).get("nodes"):
        layer = isometry.build(isometry_data(prj))
        p = os.path.join(outdir, "shema.svg")
        open(p, "w", encoding="utf-8").write(isometry.to_svg(layer))
        written.append(p)
        p = os.path.join(outdir, "shema_layer.json")
        json.dump(layer, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        written.append(p)

    return written


def main():
    ap = argparse.ArgumentParser(description="Единый проектный файл: проверка, эмиссия, прогон")
    ap.add_argument("input", help="project.json")
    ap.add_argument("--emit", choices=["heatloss", "hydro", "isometry"],
                    help="вывести вход для инструмента")
    ap.add_argument("--check", action="store_true", help="сквозная проверка согласованности")
    ap.add_argument("--run", action="store_true", help="прогнать весь конвейер")
    ap.add_argument("--outdir", default="out", help="каталог результатов для --run")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        prj = json.load(f)

    if args.emit:
        data = {"heatloss": heatloss_data, "hydro": hydro_data,
                "isometry": isometry_data}[args.emit](prj)
        json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return

    if args.check or (not args.run):
        ok, issues = check(prj)
        print("Проверка проекта «%s»:" % prj.get("meta", {}).get("object", ""), file=sys.stderr)
        for s in ok:
            print("  ✓ %s" % s, file=sys.stderr)
        for s in issues:
            print("  ✗ %s" % s, file=sys.stderr)
        print("Итог: %s" % ("замечаний нет" if not issues
                            else "%d замечаний" % len(issues)), file=sys.stderr)
        if issues and not args.run:
            sys.exit(1)

    if args.run:
        written = run_all(prj, args.outdir)
        print("Собрано:", file=sys.stderr)
        for p in written:
            print("  %s" % p, file=sys.stderr)


if __name__ == "__main__":
    main()
