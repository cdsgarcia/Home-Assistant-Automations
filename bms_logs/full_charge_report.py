"""Build full_charge_report.xlsx: one row per day on which any pack reached 100% SOC.

Sheets: Summary, CST, M1, S1, S2 - newest day on top. The workbook is regenerated from
history.json + samples_*.csv on every run (bms_log_summary.py calls it after each log),
so a newly processed day always lands at the top and re-processing never duplicates rows.

Usage: python full_charge_report.py
"""
import collections, csv, glob, json, os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "full_charge_report.xlsx")
PACKS = ["CST", "M1", "S1", "S2"]
BAL_START_V = 3.40  # Δ is only meaningful near the top; below this the curve is flat
OVP_V = 3.645       # cell OVP trip (3.650 V) as seen in 1 mV samples

HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
HEAD_FONT = Font(bold=True, color="FFFFFF")
TRIP_FILL = PatternFill("solid", fgColor="F8CBAD")
WRAP = Alignment(wrap_text=True, vertical="top")


def day_stats():
    """Per (day, pack): max SOC, top-of-charge Δ, and highest/2nd-highest cell at the day's peak."""
    st = {}
    for f in sorted(glob.glob(os.path.join(HERE, "samples_*.csv"))):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            if r.get("event"):
                continue
            try:
                c = [float(r[f"c{i}"]) for i in range(1, 17)]
                soc = int(float(r["soc"]))
                cur = float(r["current"])
            except (KeyError, TypeError, ValueError):
                continue
            t = st.setdefault((r["time"][:10], r["pack"]), {"soc": 0, "pk": 0.0, "d": None})
            t["soc"] = max(t["soc"], soc)
            hi = max(c)
            if hi > t["pk"]:
                i1 = c.index(hi)
                i2 = max((i for i in range(16) if i != i1), key=lambda i: c[i])
                t.update(pk=hi, cell=i1 + 1, c2=c[i2], cell2=i2 + 1,
                         gap=round((hi - c[i2]) * 1000), time=r["time"][11:19], cur=cur)
            if hi >= BAL_START_V:
                d = round((hi - min(c)) * 1000)
                if t["d"] is None or d > t["d"]:
                    t["d"] = d
    return st


def day_history():
    """Per day: summed balancing minutes per pack (None = not tracked yet), settings, notes."""
    hist = json.load(open(os.path.join(HERE, "history.json"), encoding="utf-8"))
    bal, tracked = collections.defaultdict(float), set()
    settings, notes = collections.defaultdict(list), collections.defaultdict(list)
    for e in sorted(hist, key=lambda e: e["start"]):
        day = e["start"][:10]
        for p, v in e["packs"].items():
            b = v.get("balancing")
            if isinstance(b, dict):
                tracked.add(day)
                bal[(day, p)] += b.get("total_min") or 0
        for lst, val in ((settings[day], e.get("settings")), (notes[day], e.get("note"))):
            if val and val not in lst:
                lst.append(val)
    return bal, tracked, settings, notes


def header(ws, cols):
    ws.append([c[0] for c in cols])
    for i, (_, width) in enumerate(cols, 1):
        cell = ws.cell(row=1, column=i)
        cell.fill, cell.font, cell.alignment = HEAD_FILL, HEAD_FONT, Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "B2"


def build():
    st = day_stats()
    bal, tracked, settings, notes = day_history()
    days = sorted({d for (d, _), v in st.items() if v["soc"] >= 100}, reverse=True)

    def bal_min(day, p):
        return round(bal[(day, p)]) if day in tracked else None

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    cols = [("Date", 12), ("Charge setting", 45)]
    for p in PACKS:
        cols += [(f"{p} max SOC %", 9), (f"{p} max Δ mV", 9), (f"{p} bal min", 9),
                 (f"{p} highest cell", 10), (f"{p} highest V", 9), (f"{p} gap mV", 8)]
    cols += [("Flags", 24), ("Note", 60)]
    header(ws, cols)
    for day in days:
        row, flags = [day, "\n".join(settings[day])], []
        for p in PACKS:
            t = st.get((day, p))
            if not t:
                row += [None] * 6
                continue
            row += [t["soc"], t["d"], bal_min(day, p), f"#{t['cell']}", t["pk"], t["gap"]]
            if t["pk"] >= OVP_V:
                flags.append(f"{p} OVP trip #{t['cell']}")
            if t["soc"] < 100:
                flags.append(f"{p} only {t['soc']}%")
        if day not in tracked:
            flags.append("balancing not tracked")
        ws.append(row + ["; ".join(flags), "\n".join(notes[day])])
        r = ws.max_row
        for i in range(1, len(cols) + 1):
            ws.cell(row=r, column=i).alignment = WRAP
        for j, p in enumerate(PACKS):
            v = ws.cell(row=r, column=3 + j * 6 + 4)
            if isinstance(v.value, float) and v.value >= OVP_V:
                v.fill = TRIP_FILL

    pcols = [("Date", 12), ("Max SOC %", 9), ("Max Δ mV (≥3.40 V)", 11), ("Bal min", 8),
             ("Chg A at peak", 9), ("Highest cell", 9), ("Highest V", 9), ("2nd highest cell", 10),
             ("2nd highest V", 9), ("Gap mV", 8), ("Peak time", 10), ("Flags", 24)]
    for p in PACKS:
        ws = wb.create_sheet(p)
        header(ws, pcols)
        for day in days:
            t = st.get((day, p))
            if not t:
                continue
            flags = []
            if t["pk"] >= OVP_V:
                flags.append("OVP trip")
            if t["soc"] < 100:
                flags.append(f"only {t['soc']}%")
            if day not in tracked:
                flags.append("balancing not tracked")
            ws.append([day, t["soc"], t["d"], bal_min(day, p), t["cur"], f"#{t['cell']}", t["pk"],
                       f"#{t['cell2']}", t["c2"], t["gap"], t["time"], "; ".join(flags)])
            if t["pk"] >= OVP_V:
                ws.cell(row=ws.max_row, column=7).fill = TRIP_FILL

    for ws in wb.worksheets:
        for col in ws.iter_cols(min_row=1):
            fmt = "0.000" if col[0].value.endswith(" V") else "0.00"
            for c in col[1:]:
                if isinstance(c.value, float):
                    c.number_format = fmt
    wb.save(OUT)
    return OUT, len(days)


if __name__ == "__main__":
    out, n = build()
    print(f"{out}: {n} full-charge days")
