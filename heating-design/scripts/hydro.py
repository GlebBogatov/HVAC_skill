#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Гидравлика тёплого пола: потери по петлям (Колбрук — Уайт), индексная петля,
рабочая точка насосно-смесительного узла (НСУ).

Считать скриптом, а не в уме: индексную (самую неблагоприятную) петлю легко
назначить не ту, а от неё зависит напор насоса. Скрипт прогоняет каждую петлю
коллектора, ловит малые петли (v < 0,15 м/с — завоздушивание), находит индексную
и собирает напор НСУ по пути худшей петли.

    python3 hydro.py collectors.json --md  > gidravlika.md
    python3 hydro.py collectors.json --json > gidravlika.json

Формат входного JSON и методика — references/teplyj-pol.md, § 8.
Длины петель берутся из spiral.py (length_total_m), мощности — из heatloss.py.
Зависимостей нет, только стандартная библиотека.
"""
from __future__ import annotations
import argparse
import json
import math
import sys

V_FLOOR = 0.15          # нижняя граница скорости, м/с (ниже — завоздушивание)
CP = 4187.0             # теплоёмкость воды, Дж/(кг·К)
G = 9.81                # м/с²

# Свойства воды: плотность и кинематическая вязкость по температуре
_T = [20, 25, 30, 35, 40, 45, 50, 55, 60]
_RHO = [998.2, 997.0, 995.6, 994.0, 992.2, 990.2, 988.0, 985.7, 983.2]
_NU = [1.004e-6, 0.893e-6, 0.801e-6, 0.725e-6, 0.658e-6,
       0.602e-6, 0.553e-6, 0.511e-6, 0.475e-6]


def _interp(x, X, Y):
    if x <= X[0]:
        return Y[0]
    if x >= X[-1]:
        return Y[-1]
    for i in range(len(X) - 1):
        if X[i] <= x <= X[i + 1]:
            k = (x - X[i]) / (X[i + 1] - X[i])
            return Y[i] + k * (Y[i + 1] - Y[i])
    return Y[-1]


def water(t):
    return _interp(t, _T, _RHO), _interp(t, _T, _NU)


def colebrook(Re, rel):
    """Коэффициент трения: ламинар 64/Re, турбулент — итерация Колбрука — Уайта."""
    if Re < 2300:
        return 64.0 / Re if Re > 0 else 0.0
    f = 0.02
    for _ in range(80):
        f = (1.0 / (-2.0 * math.log10(rel / 3.7 + 2.51 / (Re * math.sqrt(f))))) ** 2
    return f


def pipe(G_kgh, d_mm, L_m, t, ke=0.007, kloc=1.15):
    """Потери давления в трубе, кПа. kloc — надбавка на местные (по умолч. 15 %)."""
    rho, nu = water(t)
    d = d_mm / 1000.0
    A = math.pi * d * d / 4.0
    v = G_kgh / 3600.0 / rho / A if A > 0 else 0.0
    Re = v * d / nu if nu > 0 else 0.0
    f = colebrook(Re, ke / d_mm)
    dp = f * (L_m / d) * rho * v * v / 2.0 * kloc / 1000.0 if d > 0 else 0.0
    return {"v": v, "Re": Re, "f": f, "dp": dp}


def flow_from_power(Q_W, dt, t):
    """Расход из мощности и Δt, кг/ч."""
    return Q_W / (CP * dt) * 3600.0


def kvs_dp(Q_m3h, kvs):
    """Потери на арматуре по пропускной способности Kvs, кПа."""
    return (Q_m3h / kvs) ** 2 * 100.0 if kvs > 0 else 0.0


# --------------------------------------------------------------------------- #
#  Подбор насоса по семейству кривых Q–H
# --------------------------------------------------------------------------- #

# Кривые Q–H как точки (расход м³/ч, напор м) — сняты с рабочих полей.
# Как у Valtec: рабочую точку (Q, H) накладывают на семейство и берут наименьший
# насос, чья кривая проходит выше точки. 25/N и 30/N — одинаковые кривые.
# Ориентир, перед выпуском сверять с актуальным паспортом производителя.
PUMP_CATALOG = [
    ("WILO Star RS 25/2 (=30/2)", [(0, 1.9), (0.5, 1.65), (1.0, 1.35), (1.5, 0.95), (2.0, 0.35)]),
    ("WILO Star RS 25/4 (=30/4)", [(0, 4.2), (0.5, 3.7), (1.0, 3.2), (1.5, 2.6), (2.0, 1.9), (2.5, 1.1)]),
    ("WILO Star RS 25/6 (=30/6)", [(0, 5.6), (0.5, 5.1), (1.0, 4.5), (1.5, 3.8), (2.0, 3.0), (2.5, 2.1), (3.0, 1.1)]),
    ("WILO Star RS 25/7 (=30/7)", [(0, 6.5), (0.5, 6.1), (1.0, 5.5), (1.5, 4.8), (2.0, 4.0), (2.5, 3.1), (3.0, 2.1)]),
    ("Grundfos UPS 25-40",        [(0, 4.0), (0.5, 3.5), (1.0, 3.0), (1.5, 2.4), (2.0, 1.6), (2.5, 0.7)]),
    ("Grundfos UPS 25-60",        [(0, 6.0), (0.5, 5.5), (1.0, 4.9), (1.5, 4.1), (2.0, 3.2), (2.5, 2.2), (3.0, 1.0)]),
]


def _curve_H(curve, Q):
    """Напор кривой при расходе Q (линейная интерполяция); вне диапазона — 0."""
    if Q <= curve[0][0]:
        return curve[0][1]
    if Q >= curve[-1][0]:
        return 0.0
    for i in range(len(curve) - 1):
        q0, h0 = curve[i]
        q1, h1 = curve[i + 1]
        if q0 <= Q <= q1:
            return h0 + (h1 - h0) * (Q - q0) / (q1 - q0)
    return 0.0


def select_pump(Q_m3h, H_m, catalog=None):
    """Наименьший насос семейства, чья кривая перекрывает точку (Q, H)."""
    for name, curve in (catalog or PUMP_CATALOG):
        if _curve_H(curve, Q_m3h) >= H_m:
            return name, round(_curve_H(curve, Q_m3h), 2)
    return None, None


# --------------------------------------------------------------------------- #

def run_collector(coll):
    t = float(coll["t_mean"])
    d_loop = float(coll.get("d_loop_mm", 12.0))
    dt = float(coll.get("dt", 5.0))
    ke = float(coll.get("ke", 0.007))
    rho, _ = water(t)

    rows = []
    Gsum = 0.0
    idx = None
    for lp in coll["loops"]:
        Q = float(lp["Q_W"])
        L = float(lp["length_m"])
        Gk = flow_from_power(Q, dt, t)
        r = pipe(Gk, d_loop, L, t, ke)
        note = ""
        if r["v"] < V_FLOOR:
            # поднять расход до v=0,15; фактический Δt падает
            A = math.pi * (d_loop / 1000.0) ** 2 / 4.0
            Gk = V_FLOOR * A * rho * 3600.0
            dt_eff = Q / (CP * Gk / 3600.0)
            r = pipe(Gk, d_loop, L, t, ke)
            note = "малая петля: v→0,15 (Δt=%.1f К)" % dt_eff
        Gsum += Gk
        rows.append({"name": lp["name"], "room": lp.get("room", ""),
                     "L": L, "Q": Q, "G": Gk, "note": note, **r})
        if idx is None or r["dp"] > rows[idx]["dp"]:
            idx = len(rows) - 1

    Qm3 = Gsum / rho
    return {"name": coll.get("name", ""), "t": t, "d_loop": d_loop, "dt": dt,
            "rows": rows, "Gsum": Gsum, "Qm3": Qm3, "idx": idx}


def pump_point(coll, res):
    """Рабочая точка НСУ: сумма потерь по пути индексной петли + 15 %."""
    t = res["t"]
    rho, _ = water(t)
    Qm3 = res["Qm3"]
    ir = res["rows"][res["idx"]]
    Qidx_m3 = ir["G"] / rho

    parts = [("индексная петля %s" % ir["name"], ir["dp"])]
    if coll.get("kvs_flowmeter"):
        parts.append(("расходомер петли Kvs%.1f" % coll["kvs_flowmeter"],
                      kvs_dp(Qidx_m3, float(coll["kvs_flowmeter"]))))
    if coll.get("kvs_manifold"):
        parts.append(("тело коллектора Kvs%.0f" % coll["kvs_manifold"],
                      kvs_dp(Qm3, float(coll["kvs_manifold"]))))
    if coll.get("kvs_valve"):
        parts.append(("3-ход. клапан Kvs%.1f" % coll["kvs_valve"],
                      kvs_dp(Qm3, float(coll["kvs_valve"]))))
    if coll.get("fitting"):
        fd = float(coll["fitting"]["d_mm"])
        zeta = float(coll["fitting"]["zeta"])
        fr = pipe(res["Gsum"], fd, 0.0, t)
        dp_fit = zeta * rho * fr["v"] ** 2 / 2.0 / 1000.0
        parts.append(("арматура узла ζ=%.0f Dвн%.1f" % (zeta, fd), dp_fit))
    if coll.get("mains"):
        m = coll["mains"]
        rm = pipe(res["Gsum"], float(m["d_mm"]), float(m["length_m"]), t)
        parts.append(("%s Dвн%.1f %.0f м" % (m.get("name", "магистраль"),
                      float(m["d_mm"]), float(m["length_m"])), rm["dp"]))

    tot = sum(p[1] for p in parts)
    reserve = float(coll.get("reserve", 0.15))
    tot_r = tot * (1.0 + reserve)
    H = tot_r / G
    pump, pump_H = select_pump(Qm3, H, coll.get("pump_catalog"))
    return {"parts": parts, "total": tot, "total_reserved": tot_r,
            "reserve": reserve, "Q_m3h": Qm3, "H_m": H,
            "setpoint_m": math.ceil(H * 10) / 10 + 0.3,
            "pump": pump, "pump_H": pump_H}


# --------------------------------------------------------------------------- #

def to_markdown(data):
    o = []
    a = o.append
    a("# Гидравлический расчёт тёплого пола — %s" % data.get("project", ""))
    a("")
    for coll in data["collectors"]:
        res = run_collector(coll)
        a("## %s" % res["name"])
        a("")
        a("Средняя температура теплоносителя %.1f °C, труба Dвн %.1f мм, Δt %.0f К."
          % (res["t"], res["d_loop"], res["dt"]))
        a("")
        a("| Петля | Помещение | L, м | Q, Вт | G, кг/ч | v, м/с | Re | f | Δp, кПа | Примечание |")
        a("|---|---|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(res["rows"]):
            mark = " ← индексная" if i == res["idx"] else ""
            a("| %s | %s | %.1f | %.0f | %.1f | %.3f | %.0f | %.3f | %.2f | %s%s |"
              % (r["name"], r["room"], r["L"], r["Q"], r["G"], r["v"],
                 r["Re"], r["f"], r["dp"], r["note"], mark))
        a("")
        ir = res["rows"][res["idx"]]
        a("Суммарный расход коллектора Σ = %.0f кг/ч = **%.3f м³/ч**; индексная "
          "петля **%s**, Δp = **%.2f кПа**." % (res["Gsum"], res["Qm3"], ir["name"], ir["dp"]))
        a("")
        pp = pump_point(coll, res)
        a("**Рабочая точка насоса НСУ** (путь индексной петли):")
        a("")
        a("| Участок | Δp, кПа |")
        a("|---|---|")
        for n, x in pp["parts"]:
            a("| %s | %.2f |" % (n, x))
        a("| **Σ** | **%.2f** |" % pp["total"])
        a("| +%.0f %% запас | %.2f |" % (pp["reserve"] * 100, pp["total_reserved"]))
        a("")
        a("**Насос: Q = %.2f м³/ч, H = %.2f м** (режим Δp-c, уставка ≈ %.1f м)."
          % (pp["Q_m3h"], pp["H_m"], pp["setpoint_m"]))
        if pp.get("pump"):
            a("Принят по рабочему полю кривых: **%s** (даёт %.2f м при рабочем расходе)."
              % (pp["pump"], pp["pump_H"]))
        else:
            a("Подходящего насоса в каталоге нет — рабочая точка выше кривых, "
              "проверить потери или взять более мощную линейку.")
        a("")

        # балансировка: настройка расходомеров по петлям
        rho, _ = water(res["t"])
        idp = res["rows"][res["idx"]]["dp"]
        a("**Балансировка (настройка расходомеров коллектора):**")
        a("")
        a("| Петля | Помещение | Расход, л/мин | Δp петли, кПа | Гасить расходомером, кПа |")
        a("|---|---|---|---|---|")
        for i, r in enumerate(res["rows"]):
            lmin = r["G"] / rho / 60.0 * 1000.0
            excess = idp - r["dp"]
            mark = " (индексная — расходомер открыт)" if i == res["idx"] else ""
            a("| %s | %s | %.2f | %.2f | %.2f%s |"
              % (r["name"], r["room"], lmin, r["dp"], max(0.0, excess), mark))
        a("")
        a("Индексную петлю оставить открытой, остальные придушить расходомером на "
          "указанный избыток напора (Δp индексной − Δp петли), выставив её расчётный расход.")
        a("")
    return "\n".join(o)


def to_json(data):
    out = {"project": data.get("project", ""), "collectors": []}
    for coll in data["collectors"]:
        res = run_collector(coll)
        pp = pump_point(coll, res)
        out["collectors"].append({
            "name": res["name"], "Q_m3h": round(res["Qm3"], 3),
            "index_loop": res["rows"][res["idx"]]["name"],
            "index_dp_kpa": round(res["rows"][res["idx"]]["dp"], 2),
            "pump": {"Q_m3h": round(pp["Q_m3h"], 3), "H_m": round(pp["H_m"], 2),
                     "setpoint_m": pp["setpoint_m"], "model": pp.get("pump")},
            "loops": [{"name": r["name"], "room": r["room"], "L_m": r["L"],
                       "Q_W": r["Q"], "G_kgh": round(r["G"], 1),
                       "flow_lmin": round(r["G"] / water(res["t"])[0] / 60.0 * 1000.0, 2),
                       "v_ms": round(r["v"], 3), "dp_kpa": round(r["dp"], 2),
                       "throttle_kpa": round(max(0.0, res["rows"][res["idx"]]["dp"] - r["dp"]), 2),
                       "note": r["note"]} for r in res["rows"]],
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="Гидравлика ТП: индексная петля и подбор НСУ")
    ap.add_argument("input", help="JSON с коллекторами и петлями")
    ap.add_argument("--md", action="store_true", help="вывод markdown (по умолчанию)")
    ap.add_argument("--json", action="store_true", help="вывод JSON")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    if args.json:
        json.dump(to_json(data), sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(to_markdown(data))


if __name__ == "__main__":
    main()
