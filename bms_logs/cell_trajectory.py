"""Plot highest-cell trajectory per pack vs inverter battery voltage from an HA history export.

Usage: python cell_trajectory.py <history.json> <out.png> [--tz 8] [--title "..."] [--span "HH:MM-HH:MM=label" ...]
history.json = result of HA websocket `history/history_during_period` (minimal_response, no_attributes)
for sensor.srne_battery_voltage and sensor.<pack>_ble_highest_cell_voltage.
"""
import argparse
import datetime as dt
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

PACKS = ("cst", "m1", "s1", "s2")
OVP, FULL = 3.65, 3.60


def series(hist, entity, tz):
    out = []
    for x in hist.get(entity, []):
        try:
            out.append((dt.datetime.fromtimestamp(x["lu"], dt.timezone(dt.timedelta(hours=tz))), float(x["s"])))
        except (ValueError, KeyError):
            pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("history")
    ap.add_argument("out")
    ap.add_argument("--tz", type=float, default=8)
    ap.add_argument("--title", default="Highest cell per pack vs inverter charge voltage")
    ap.add_argument("--span", action="append", default=[], help='Shade a period: "HH:MM-HH:MM=label" (repeatable)')
    a = ap.parse_args()
    with open(a.history, encoding="utf-8-sig") as f:
        hist = json.load(f)
    hist = hist.get("result", hist)
    tzinfo = dt.timezone(dt.timedelta(hours=a.tz))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1.4]})
    for p in PACKS:
        s = series(hist, f"sensor.{p}_ble_highest_cell_voltage", a.tz)
        if s:
            ax1.step(*zip(*s), where="post", label=p.upper(), lw=1.4)
    ax1.axhline(OVP, color="red", ls="--", lw=1, label="Cell OVP 3.65 V")
    ax1.axhline(FULL, color="orange", ls=":", lw=1, label="Cell full 3.60 V")
    ax1.set_ylabel("Highest cell (V)")
    ax1.set_ylim(3.34, 3.67)
    ax1.grid(alpha=0.3)
    ax1.legend(loc="upper left", ncol=3, fontsize=9)
    ax1.set_title(a.title)

    inv = series(hist, "sensor.srne_battery_voltage", a.tz)
    if inv:
        ax2.step(*zip(*inv), where="post", color="black", lw=1.2, label="Inverter battery V")
    for lvl, c in ((56.4, "green"), (56.8, "red")):
        ax2.axhline(lvl, color=c, ls="--", lw=0.8, label=f"{lvl} V")
    ax2.set_ylabel("Inverter V")
    ax2.grid(alpha=0.3)
    ax2.legend(loc="upper left", fontsize=9)
    if inv:
        day = inv[0][0].date()
        for i, span in enumerate(a.span):
            rng, label = span.split("=", 1)
            t0, t1 = (dt.datetime.combine(day, dt.time.fromisoformat(x), tzinfo) for x in rng.split("-"))
            for ax in (ax1, ax2):
                ax.axvspan(t0, t1, color=f"C{i % 10}", alpha=0.08)
            ax1.text(t0, 3.665 - 0.012 * (i % 2), label, fontsize=8, va="top")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tzinfo))
    fig.tight_layout()
    fig.savefig(a.out, dpi=110)
    print(a.out)


if __name__ == "__main__":
    main()
