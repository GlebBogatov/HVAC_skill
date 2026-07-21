#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Расчёт теплопотерь здания по СП 50.13330.2012 / СП 60.13330.2020.

Вход  — JSON с климатом, конструкциями и помещениями (см. references/teplopoteri.md).
Выход — markdown-таблица (--md) или JSON (--json), плюс блок самопроверки.

    python3 heatloss.py project.json --md > teplopoteri.md
    python3 heatloss.py project.json --json > teplopoteri.json

Скрипт сознательно многословен в проверках: потерянная надбавка на одном
помещении из десяти в ручной таблице не видна, а здесь она вылезает в контроле.
"""
from __future__ import annotations
import argparse
import json
import math
import sys

R_SI = 1.0 / 8.7          # внутренняя теплоотдача, стены и полы
R_SE = 1.0 / 23.0         # наружная теплоотдача, наружный воздух
R_SE_VENT = 1.0 / 8.7     # вентфасад, чердак, подполье

ZONE_R = [2.1, 4.3, 8.6, 14.2]   # СП 50, метод зон
ZONE_W = 2.0                      # ширина зоны, м

# Rтр = a * ГСОП + b, таблица 3 СП 50.13330.2012
RTR = {
    1: {"wall": (0.00035, 1.4), "roof": (0.00045, 1.9),
        "floor": (0.00045, 1.9), "window": (0.000075, 0.15)},
    2: {"wall": (0.0003, 1.2), "roof": (0.0004, 1.6),
        "floor": (0.00035, 1.3), "window": (0.00005, 0.3)},
    3: {"wall": (0.0002, 1.0), "roof": (0.00025, 1.5),
        "floor": (0.0002, 1.0), "window": (0.000025, 0.2)},
}

N_CONTACT = {"outdoor": 1.0, "attic": 0.9, "unheated": 0.6, "underfloor": 0.4}


# --------------------------------------------------------------------------- #
#  Конструкции
# --------------------------------------------------------------------------- #

def constr_R(c: dict) -> float:
    """R₀ конструкции: либо готовое значение, либо по слоям."""
    if "R" in c:
        return float(c["R"])
    layers = c.get("layers")
    if not layers:
        raise ValueError("конструкция без R и без layers")
    r_si = float(c.get("R_si", R_SI))
    r_se = float(c.get("R_se", R_SE))
    r = r_si + r_se + sum(float(d) / float(lam) for d, lam in layers)
    return r * float(c.get("r", 1.0))


def gsop(climate: dict, t_in: float) -> float:
    return (t_in - float(climate["t_ot"])) * float(climate["z_ot"])


def r_required(group: int, kind: str, g: float):
    ab = RTR.get(int(group), {}).get(kind)
    if not ab:
        return None
    a, b = ab
    return a * g + b


# --------------------------------------------------------------------------- #
#  Метод зон
# --------------------------------------------------------------------------- #

def rect_from(perimeter: float, area: float):
    """Прямоугольник a×b с заданными периметром и площадью (для разбивки полос)."""
    s = perimeter / 2.0
    disc = s * s - 4.0 * area
    if disc < 0:
        # форма далека от прямоугольной — берём квадрат равной площади
        a = b = math.sqrt(area)
        return a, b, False
    root = math.sqrt(disc)
    return (s + root) / 2.0, (s - root) / 2.0, True


def inner_area(a: float, b: float, d: float) -> float:
    return max(0.0, a - 2 * d) * max(0.0, b - 2 * d)


def zone_split(ground: dict):
    """
    Разбивка заглублённой стены и пола по грунту на зоны.
    Развёртка отсчитывается от уровня земли: сначала вниз по стене, затем внутрь по полу.
    Возвращает список словарей по зонам.
    """
    P = float(ground.get("perimeter", 0.0))
    h = float(ground.get("wall_depth", 0.0))
    A = float(ground.get("floor_area", 0.0))
    r_wall_add = float(ground.get("R_add_wall", 0.0))
    r_floor_add = float(ground.get("R_add_floor", 0.0))
    corner_double = bool(ground.get("corner_double", False))

    a, b, rect_ok = rect_from(P, A) if A > 0 else (0.0, 0.0, True)

    rows = []
    for k in range(4):
        lo, hi = k * ZONE_W, (k + 1) * ZONE_W
        if k == 3:
            hi = float("inf")

        # часть стены, попавшая в зону
        w_lo, w_hi = max(lo, 0.0), min(hi, h)
        wall_a = P * max(0.0, w_hi - w_lo)

        # часть пола: развёртка d_floor = развёртка - h
        f_lo, f_hi = max(0.0, lo - h), max(0.0, hi - h)
        if A > 0 and f_hi > f_lo:
            if math.isinf(f_hi):
                floor_a = inner_area(a, b, f_lo)
            else:
                floor_a = inner_area(a, b, f_lo) - inner_area(a, b, f_hi)
            if corner_double and k == 0 and not math.isinf(f_hi):
                floor_a += 4.0 * (f_hi - f_lo) ** 2
        else:
            floor_a = 0.0

        if wall_a <= 1e-9 and floor_a <= 1e-9:
            continue
        rows.append({
            "zone": k + 1,
            "R_base": ZONE_R[k],
            "wall_area": round(wall_a, 3),
            "wall_R": round(ZONE_R[k] + r_wall_add, 3),
            "floor_area": round(floor_a, 3),
            "floor_R": round(ZONE_R[k] + r_floor_add, 3),
        })
    return rows, {"a": a, "b": b, "rect_ok": rect_ok}


# --------------------------------------------------------------------------- #
#  Помещение
# --------------------------------------------------------------------------- #

def room_losses(room: dict, constructions: dict, climate: dict, group: int):
    t_in = float(room["t_in"])
    t_out = float(climate["t_out"])
    dt_out = t_in - t_out
    items = []
    q_trans = 0.0

    for e in room.get("envelopes", []):
        name = e["constr"]
        c = constructions[name]
        R = constr_R(c)
        U = 1.0 / R
        A = float(e["area"])
        beta = float(e.get("beta", 1.0))
        contact = e.get("contact", "outdoor")

        if "t_adj" in e:
            dt = t_in - float(e["t_adj"])
            n = 1.0
        else:
            n = float(e.get("n", N_CONTACT.get(contact, 1.0)))
            dt = dt_out

        q = A * U * dt * n * beta
        note = ""

        di = e.get("door_infiltration")
        if di:
            k = float(di.get("k", 0.22))
            H = float(di["H"])
            add = q * k * H
            note = "врывание +%.0f Вт (%.2f·H)" % (add, k)
            q += add

        q_trans += q
        items.append({
            "constr": name, "area": round(A, 2), "R": round(R, 3),
            "U": round(U, 3), "dt": round(dt, 1), "n": n, "beta": beta,
            "Q": round(q), "note": note,
            "kind": c.get("kind"),
        })

    # грунт
    ground_rows = []
    ground_geom = None
    if room.get("ground"):
        ground_rows, ground_geom = zone_split(room["ground"])
        for r in ground_rows:
            qw = r["wall_area"] * dt_out / r["wall_R"]
            qf = r["floor_area"] * dt_out / r["floor_R"]
            r["Q_wall"] = round(qw)
            r["Q_floor"] = round(qf)
            q_trans += qw + qf

    # вентиляция
    v = room.get("vent") or {}
    mode = v.get("mode", "ach")
    val = float(v.get("value", 0.0))
    area = float(room.get("area", 0.0))
    height = float(room.get("height", 0.0))
    volume = area * height
    if mode == "ach":
        L = val * volume
    elif mode == "flow":
        L = val
    elif mode == "per_area":
        L = val * area
    else:
        raise ValueError("vent.mode: ach | flow | per_area")
    rho = 353.0 / (273.0 + t_in)
    q_vent = 0.28 * L * rho * dt_out

    gains = float(room.get("gains", 0.0))
    total = q_trans + q_vent - gains

    return {
        "name": room["name"], "area": round(area, 2), "volume": round(volume, 2),
        "t_in": t_in, "items": items, "ground": ground_rows, "ground_geom": ground_geom,
        "L": round(L, 1), "rho": round(rho, 3),
        "Q_trans": round(q_trans), "Q_vent": round(q_vent),
        "gains": round(gains), "Q": round(total),
        "q_sp": round(total / area, 1) if area else None,
    }


# --------------------------------------------------------------------------- #
#  Расчёт целиком
# --------------------------------------------------------------------------- #

def calculate(data: dict):
    climate = data["climate"]
    constructions = data["constructions"]
    group = int(data.get("building_group", 2))

    rooms = [room_losses(r, constructions, climate, group) for r in data["rooms"]]

    total_trans = sum(r["Q_trans"] for r in rooms)
    total_vent = sum(r["Q_vent"] for r in rooms)
    total_gains = sum(r["gains"] for r in rooms)
    total = sum(r["Q"] for r in rooms)
    total_area = sum(r["area"] for r in rooms)

    # проверка тепловой защиты
    t_ref = max(float(r["t_in"]) for r in data["rooms"])
    g = gsop(climate, t_ref)
    envelope_check = []
    for name, c in constructions.items():
        kind = c.get("kind")
        if not kind:
            continue
        R = constr_R(c)
        Rtr = r_required(group, kind, g)
        if Rtr is None:
            continue
        envelope_check.append({
            "constr": name, "kind": kind, "R": round(R, 3),
            "R_req": round(Rtr, 3), "ok": R >= Rtr - 1e-9,
        })

    # контроль зон
    zone_check = []
    for src, res in zip(data["rooms"], rooms):
        if not res["ground"]:
            continue
        declared = float(src["ground"].get("floor_area", 0.0))
        summed = sum(z["floor_area"] for z in res["ground"])
        dev = abs(summed - declared) / declared * 100 if declared else 0.0
        zone_check.append({
            "room": res["name"], "declared": round(declared, 2),
            "zones_sum": round(summed, 2), "dev_pct": round(dev, 2),
            "ok": dev < 0.5, "rect_ok": res["ground_geom"]["rect_ok"],
        })

    # замыкание баланса
    comp_sum = 0.0
    for r in rooms:
        comp_sum += sum(i["Q"] for i in r["items"])
        comp_sum += sum(z.get("Q_wall", 0) + z.get("Q_floor", 0) for z in r["ground"])
        comp_sum += r["Q_vent"] - r["gains"]

    return {
        "project": data.get("project", ""),
        "climate": climate,
        "building_group": group,
        "GSOP": round(g),
        "rooms": rooms,
        "totals": {
            "area": round(total_area, 2),
            "Q_trans": round(total_trans), "Q_vent": round(total_vent),
            "gains": round(total_gains), "Q": round(total),
            "Q_reserve_10": round(total * 1.1),
            "q_sp": round(total / total_area, 1) if total_area else None,
        },
        "checks": {
            "envelope": envelope_check,
            "zones": zone_check,
            "balance": {
                "by_rooms": round(total), "by_components": round(comp_sum),
                "dev": round(abs(total - comp_sum)),
                "ok": abs(total - comp_sum) < max(2.0, 0.001 * abs(total)),
            },
        },
    }


# --------------------------------------------------------------------------- #
#  Вывод
# --------------------------------------------------------------------------- #

def to_markdown(res: dict) -> str:
    o = []
    a = o.append
    a("# Расчёт теплопотерь — %s" % res["project"])
    a("")
    c = res["climate"]
    a("tн = %s °C, tот = %s °C, zот = %s сут, ГСОП = %s °C·сут, группа зданий %s"
      % (c["t_out"], c["t_ot"], c["z_ot"], res["GSOP"], res["building_group"]))
    a("")
    a("## Результаты по помещениям")
    a("")
    a("| Помещение | S, м² | tв, °C | Qтрансм, Вт | Qвент, Вт | Теплопост., Вт | Qсумм, Вт | Вт/м² |")
    a("|---|---|---|---|---|---|---|---|")
    for r in res["rooms"]:
        a("| %s | %.2f | %g | %d | %d | %d | **%d** | %s |"
          % (r["name"], r["area"], r["t_in"], r["Q_trans"], r["Q_vent"],
             r["gains"], r["Q"], r["q_sp"]))
    t = res["totals"]
    a("| **ВСЕГО** | **%.2f** | | **%d** | **%d** | **%d** | **%d** | **%s** |"
      % (t["area"], t["Q_trans"], t["Q_vent"], t["gains"], t["Q"], t["q_sp"]))
    a("")
    a("**Расчётная тепловая нагрузка Qо = %.2f кВт.** С запасом 10 %% — **%.1f кВт**."
      % (t["Q"] / 1000.0, t["Q_reserve_10"] / 1000.0))
    a("")

    a("## Разбивка по конструкциям")
    a("")
    for r in res["rooms"]:
        a("### %s (tв = %g °C, V = %.1f м³)" % (r["name"], r["t_in"], r["volume"]))
        a("")
        a("| Конструкция | A, м² | R | U | Δt | n | β | Q, Вт | Примечание |")
        a("|---|---|---|---|---|---|---|---|---|")
        for i in r["items"]:
            a("| %s | %.2f | %.3f | %.3f | %.1f | %g | %g | %d | %s |"
              % (i["constr"], i["area"], i["R"], i["U"], i["dt"], i["n"],
                 i["beta"], i["Q"], i["note"]))
        if r["ground"]:
            a("")
            a("Метод зон:")
            a("")
            a("| Зона | Aстены, м² | Rстены | Qстены, Вт | Aпола, м² | Rпола | Qпола, Вт |")
            a("|---|---|---|---|---|---|---|")
            for z in r["ground"]:
                a("| %d | %.2f | %.3f | %d | %.2f | %.3f | %d |"
                  % (z["zone"], z["wall_area"], z["wall_R"], z["Q_wall"],
                     z["floor_area"], z["floor_R"], z["Q_floor"]))
        a("")
        a("Вентиляция: L = %.1f м³/ч, ρ = %.3f кг/м³ → %d Вт"
          % (r["L"], r["rho"], r["Q_vent"]))
        a("")

    a("## Контроль расчёта")
    a("")
    ch = res["checks"]
    a("| Проверка | Результат |")
    a("|---|---|")
    b = ch["balance"]
    a("| Замыкание баланса (по помещениям / по конструкциям) | %d / %d Вт, расхождение %d Вт — %s |"
      % (b["by_rooms"], b["by_components"], b["dev"], "сходится" if b["ok"] else "**РАСХОЖДЕНИЕ**"))
    for z in ch["zones"]:
        a("| Сумма площадей зон = площадь пола, %s | %.2f / %.2f м², %.2f %% — %s |"
          % (z["room"], z["zones_sum"], z["declared"], z["dev_pct"],
             "сходится" if z["ok"] else "**ПРОВЕРИТЬ**"))
    for e in ch["envelope"]:
        a("| R %s против Rтр по СП 50 | %.3f / %.3f — %s |"
          % (e["constr"], e["R"], e["R_req"],
             "проходит" if e["ok"] else "**НЕ ПРОХОДИТ**"))
    sp = res["totals"]["q_sp"]
    verdict = "норма" if sp and 30 <= sp <= 140 else "**вне типичного диапазона, проверить**"
    a("| Удельная нагрузка, Вт/м² | %s — %s |" % (sp, verdict))
    a("")
    a("> Расхождения не подгонять. Если площадь по DXF не сходится с экспликацией — "
      "выяснить причину и записать её в записку.")
    return "\n".join(o)


def main():
    ap = argparse.ArgumentParser(description="Расчёт теплопотерь по СП 50/60")
    ap.add_argument("input", help="JSON с исходными данными")
    ap.add_argument("--md", action="store_true", help="вывод markdown (по умолчанию)")
    ap.add_argument("--json", action="store_true", help="вывод JSON")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    res = calculate(data)

    if args.json:
        json.dump(res, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(to_markdown(res))


if __name__ == "__main__":
    main()
