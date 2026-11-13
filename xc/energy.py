"""Consumption history of plugs and other metering devices, as the Mi cloud keeps it.

The cloud has two series per device: one value per day and one per month (v2/user/statistics). Weeks and
years are added up here. Two things are particular:
  * a "day" of the cloud starts at midnight in China (UTC+8), whatever the region of the account - the
    labels below therefore name days and months by the Chinese calendar date;
  * the unit differs by maker and is not in the device description: cuco plugs count in 0.01 kWh
    (measured: a day with 713 Wh of recorded power has the value 70), lumi (Aqara) plugs in Wh.
"""
from __future__ import annotations

import time

CHINA = 8 * 3600
DAY = 86400
# kWh per unit of the statistics value, by model prefix
KWH_PER_UNIT = {"cuco.plug.": 0.01, "lumi.": 0.001}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
PERIODS = ("day", "week", "month", "year")


def energy_source(model: str, spec) -> dict | None:
    """Where the consumption statistics of this device are: {"key", "day", "month", "kwh"} - or None if it
    doesn't measure. "kwh" is None when the unit of this maker isn't known (values are then shown as they are)."""
    model = model or ""
    ratio = next((r for prefix, r in KWH_PER_UNIT.items() if model.startswith(prefix)), None)
    for svc in getattr(spec, "services", None) or []:
        for p in svc.props:
            if p.name == "power-consumption" and p.readable:
                return {"key": f"{svc.siid}.{p.piid}", "day": "stat_day_v3", "month": "stat_month_v3", "kwh": ratio}
    if model.startswith("lumi.plug") or model.startswith("lumi.ctrl_86plug") or model.startswith("lumi.acpartner"):
        return {"key": "powerCost", "day": "stat_day", "month": "stat_month", "kwh": 0.001}
    return None


def fetch(cloud, did: str, source: dict, now: float | None = None) -> dict:
    """{"days": [(time, value)], "months": [(time, value)], "unit": "kWh" | None} - values in kWh when the
    unit is known. Blocking: run it off the UI thread."""
    now = int(now or time.time())
    days = cloud.statistics(did, source["key"], source["day"], now - 100 * DAY, now, 100)
    months = cloud.statistics(did, source["key"], source["month"], now - 3 * 366 * DAY, now, 40)
    ratio = source.get("kwh")
    scale = (lambda rows: [(t, round(v * ratio, 3)) for t, v in rows]) if ratio else (lambda rows: rows)
    return {"days": scale(days), "months": scale(months), "unit": "kWh" if ratio else None}


def _china(t: float) -> time.struct_time:
    return time.gmtime(t + CHINA)


def bars(data: dict, period: str) -> list[tuple[str, float, str]]:
    """[(short label, value, long label)] oldest first, for one of PERIODS."""
    if period == "day":
        return [(time.strftime("%d.%m.", _china(t)), v, time.strftime("%A %d.%m.%Y", _china(t)))
                for t, v in data["days"][-30:]]
    if period == "week":
        weeks: dict[tuple, list] = {}
        for t, v in data["days"]:
            d = _china(t)
            monday = t + CHINA - d.tm_wday * DAY
            key = time.strftime("%Y%m%d", time.gmtime(monday))
            weeks.setdefault(key, [monday, 0.0, 0])
            weeks[key][1] += v
            weeks[key][2] += 1
        out = []
        for _key, (monday, total, n) in sorted(weeks.items())[-12:]:
            a, b = time.gmtime(monday), time.gmtime(monday + 6 * DAY)
            label = time.strftime("%d.%m.", a)
            long = f"{time.strftime('%d.%m.', a)} - {time.strftime('%d.%m.%Y', b)}" + ("" if n == 7 else f" ({n} days)")
            out.append((label, round(total, 3), long))
        return out
    if period == "month":
        return [(f"{MONTHS[_china(t).tm_mon - 1]} {_china(t).tm_year % 100:02d}", v,
                 f"{MONTHS[_china(t).tm_mon - 1]} {_china(t).tm_year}") for t, v in data["months"][-12:]]
    if period == "year":
        years: dict[int, list] = {}
        for t, v in data["months"]:
            y = _china(t).tm_year
            years.setdefault(y, [0.0, 0])
            years[y][0] += v
            years[y][1] += 1
        return [(str(y), round(total, 3), str(y) + ("" if n == 12 else f" ({n} months)"))
                for y, (total, n) in sorted(years.items())]
    raise ValueError(period)


def number(v: float, unit: str | None) -> str:
    """12.3 kWh / 0.45 kWh / 1 234 kWh; unit "" leaves the unit out, None means the plain number as reported."""
    if unit is None:
        return f"{v:g}"
    if v >= 100:
        text = f"{v:,.0f}".replace(",", " ")
    elif v >= 10:
        text = f"{v:.1f}"
    else:
        text = f"{v:.2f}"
    return f"{text} {unit}".strip()


def summary(rows: list[tuple[str, float, str]], period: str, unit: str) -> str:
    if not rows:
        return "No consumption recorded for this period."
    total = sum(v for _l, v, _t in rows)
    top = max(rows, key=lambda r: r[1])
    what = {"day": "day", "week": "week", "month": "month", "year": "year"}[period]
    text = f"Total {number(total, unit)}  ·  average {number(total / len(rows), unit)} per {what}"
    if len(rows) > 1:
        text += f"  ·  most: {number(top[1], unit)} ({top[2]})"
    return text
