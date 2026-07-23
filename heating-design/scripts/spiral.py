#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор раскладки тёплого пола улиткой (встречная спираль, бифилярная навивка).

Труба идёт спиралью к центру с шагом 2s, в центре разворачивается и возвращается
в промежутках — подача и обратка чередуются через нитку. Это даёт равномерную
температуру поверхности и вдвое больший радиус гиба, чем змейка.

По умолчанию считается труба PE-Xa 16×2,0 (Dвн 12 мм) — типоразмер по правилу
скилла: контуры ТП внутри помещений только PE-Xa 16×2,0 (Dвн 12), труба Ø20
резервируется под уличное отопление. Отсюда предел длины петли 100 м (рабочий
ориентир 75 м) и минимальный шаг 80 мм — в улитке радиус разворота равен шагу,
а для PE-Xa Ø16 минимум составляет 5·Dн = 80 мм.

    python3 spiral.py --w 3400 --h 2280 --step 150 --offset 150 \
                      --origin 1200 800 --name С1 --supply 6.3 --json loop_S1.json

Все размеры в миллиметрах, длины в отчёте — в метрах.
Помещение сложной формы разбивается на прямоугольники, каждый считается отдельно.
"""
from __future__ import annotations
import argparse
import json
import math
import sys

# Консоль Windows по умолчанию cp1251 и падает на символах × и ² в отчёте.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def build_spiral(w: float, h: float, step: float, offset: float,
                 x0: float = 0.0, y0: float = 0.0):
    """
    Возвращает (points, n_rings). Кольца с чётными индексами проходятся внутрь,
    с нечётными — обратно наружу; между ними остаётся шаг s.
    """
    half = min(w, h) / 2.0
    usable = half - offset
    if usable <= 0:
        raise ValueError("отступ от стен больше половины размера поля")
    n = int(usable // step) + 1
    if n < 2:
        raise ValueError("поле слишком мало для улитки при заданном шаге")

    def ring(i):
        d = offset + i * step
        return (x0 + d, y0 + d, x0 + w - d, y0 + h - d)

    evens = [i for i in range(n) if i % 2 == 0]
    odds = [i for i in range(n) if i % 2 == 1][::-1]
    seq = evens + odds

    pts = []
    for i in seq:
        x1, y1, x2, y2 = ring(i)
        if x2 - x1 < step / 2 or y2 - y1 < step / 2:
            continue                      # вырожденное кольцо в центре
        loop = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        if pts:
            pts.append(loop[0])           # перемычка от предыдущего кольца
        pts.extend(loop)
    return pts, len(seq)


def polyline_length(pts) -> float:
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


def main():
    ap = argparse.ArgumentParser(description="Раскладка тёплого пола улиткой")
    ap.add_argument("--w", type=float, required=True, help="ширина поля, мм")
    ap.add_argument("--h", type=float, required=True, help="высота поля, мм")
    ap.add_argument("--step", type=float, required=True, help="шаг укладки, мм")
    ap.add_argument("--offset", type=float, default=150.0,
                    help="отступ от наружных стен, мм (по умолчанию 150)")
    ap.add_argument("--origin", type=float, nargs=2, default=[0.0, 0.0],
                    metavar=("X", "Y"), help="координаты левого нижнего угла поля, мм")
    ap.add_argument("--name", default="П1", help="марка петли")
    ap.add_argument("--room", default="", help="помещение")
    ap.add_argument("--supply", type=float, default=0.0,
                    help="длина подводок до коллектора, м (в обе стороны)")
    ap.add_argument("--limit", type=float, default=100.0,
                    help="предельная длина петли с подводками, м (по умолчанию 100 для PE-Xa 16x2,0)")
    ap.add_argument("--work-limit", type=float, default=75.0, dest="work_limit",
                    help="рабочий ориентир длины петли, м (по умолчанию 75)")
    ap.add_argument("--od", type=float, default=16.0,
                    help="наружный диаметр трубы, мм (по умолчанию 16)")
    ap.add_argument("--id", type=float, default=12.0, dest="idm",
                    help="внутренний диаметр трубы, мм (по умолчанию 12)")
    ap.add_argument("--json", help="файл для записи результата")
    args = ap.parse_args()

    # Правило скилла: труба петель ТП внутри помещений — PE-Xa 16x2,0 (Dвн 12...13).
    # Ø20 (Dвн 16) — только уличное отопление.
    if not (12.0 <= args.idm <= 13.0):
        print("ВНИМАНИЕ: Dвн %.1f мм вне диапазона 12...13 мм — труба петель ТП должна "
              "быть PE-Xa 16x2,0 (Dвн 12,0); Ø20 (Dвн 16) только для уличного отопления"
              % args.idm, file=sys.stderr)

    # В улитке радиус разворота в центре равен шагу. Для PE-Xa минимум — 5*Dн.
    r_min = 5.0 * args.od
    bend_ok = args.step >= r_min

    pts, n = build_spiral(args.w, args.h, args.step, args.offset,
                          args.origin[0], args.origin[1])
    l_field = polyline_length(pts) / 1000.0
    total = l_field + args.supply

    area = args.w * args.h / 1e6
    est_field = area / (args.step / 1000.0)    # грубая оценка L ≈ A/s по всему полю

    # Уточнённая оценка: труба реально покрывает поле за вычетом краевой полосы
    # (отступ от стен минус полшага). При отступе 150 и шаге 150 краевая полоса
    # 75 мм по периметру не обогревается — это нормально и учитывается здесь.
    margin = max(0.0, args.offset - args.step / 2.0)
    a_eff = max(0.0, (args.w - 2 * margin)) * max(0.0, (args.h - 2 * margin)) / 1e6
    est = a_eff / (args.step / 1000.0)
    dev = (l_field - est) / est * 100.0 if est else 0.0

    volume_l = total * math.pi * (args.idm / 1000.0) ** 2 / 4.0 * 1000.0

    res = {
        "name": args.name,
        "room": args.room,
        "layer": "TP-" + args.name,
        "pipe": {"od": args.od, "id": args.idm, "material": "PE-Xa",
                 "bend_radius_min_mm": r_min, "bend_radius_ok": bend_ok},
        "volume_l": round(volume_l, 1),
        "field": {"w": args.w, "h": args.h, "area_m2": round(area, 2),
                  "step": args.step, "offset": args.offset,
                  "origin": args.origin, "rings": n},
        "pts": [[round(x, 1), round(y, 1)] for x, y in pts],
        "length_field_m": round(l_field, 1),
        "length_supply_m": round(args.supply, 1),
        "length_total_m": round(total, 1),
        "area_effective_m2": round(a_eff, 2),
        "check_A_over_s_field_m": round(est_field, 1),
        "check_A_over_s_effective_m": round(est, 1),
        "dev_pct": round(dev, 1),
        "limit_m": args.limit,
        "within_limit": total <= args.limit,
    }

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)

    out = sys.stderr if args.json else sys.stdout
    print("Петля %s%s" % (args.name, (" — " + args.room) if args.room else ""), file=out)
    print("  поле %.0f×%.0f мм = %.2f м², шаг %.0f мм, колец %d"
          % (args.w, args.h, area, args.step, n), file=out)
    print("  длина по полю      %.1f м" % l_field, file=out)
    print("  подводки           %.1f м" % args.supply, file=out)
    print("  ИТОГО              %.1f м  (предел %.0f м) — %s"
          % (total, args.limit, "в допуске" if res["within_limit"] else "ПРЕВЫШЕНИЕ"), file=out)
    print("  труба              PE-Xa %.0f мм, Dвн %.1f мм, объём петли %.1f л"
          % (args.od, args.idm, volume_l), file=out)
    if not bend_ok:
        print("  радиус гиба        ШАГ %.0f мм < 5·Dн = %.0f мм — труба заломится"
              % (args.step, r_min), file=out)
    if total > args.work_limit and res["within_limit"]:
        print("  длина              выше рабочего ориентира %.0f м — проверить увязку "
              "и напор насоса" % args.work_limit, file=out)
    print("  оценка A/s по полю %.1f м (грубая, без учёта отступа)" % est_field, file=out)
    print("  контроль A/s       %.1f м по обогреваемой площади %.2f м², расхождение %+.1f %% — %s"
          % (est, a_eff, dev, "норма" if abs(dev) < 10 else "ПРОВЕРИТЬ ПАРАМЕТРЫ"), file=out)
    if args.json:
        print("  геометрия записана в %s" % args.json, file=out)


if __name__ == "__main__":
    main()
