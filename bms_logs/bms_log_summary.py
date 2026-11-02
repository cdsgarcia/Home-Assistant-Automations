"""Summarise a BMS_BLE debug log and append the result to the history files.

Usage: python bms_log_summary.py <home-assistant_bms_ble_*.log> [--settings "..."] [--note "..."]

Writes/updates (next to this script):
  history.json  - machine-readable list of capture summaries (one per log file)
  HISTORY.md    - human-readable tables, newest capture first
"""
import argparse, ast, collections, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_RE = re.compile(r"(\S+ \S+) \w+ .*?\] (\S+): BMS data sample (\{.*\})\s*$")
PAY_RE = re.compile(r"(\S+ \S+) .*0A03 payload: (.*)$")


def parse(path):
    rows, pay, errs = [], [], []
    for line in open(path, encoding="utf-8", errors="replace"):
        m = SAMPLE_RE.match(line)
        if m:
            ts, dev, d = m.groups()
            d = re.sub(r"TempSensor\(([-\d.]+), <T\.\w+: \d+>\)", r"\1", d)
            d = re.sub(r"<BMSMode\.(\w+): \d+>", r"'\1'", d)
            try:
                rows.append((ts, dev, ast.literal_eval(d)))
            except (ValueError, SyntaxError):
                errs.append((ts, "parse_err"))
            continue
        m = PAY_RE.match(line)
        if m:
            pay.append((m.group(1), m.group(2).strip()))
            continue
        m = re.match(r"(\S+ \S+) .*Error fetching (\S+) data", line)
        if m:
            errs.append(m.groups())
    return rows, pay, errs


def cst_mask(hexstr):
    """Pace 0x0A03 balance mask: payload bytes 6-7 little-endian, bit0 = cell 1."""
    b = bytes.fromhex(hexstr.replace(" ", ""))
    return int.from_bytes(b[6:8], "little") if len(b) >= 8 else 0


def bits(mask):
    return [i + 1 for i in range(16) if mask >> i & 1]


def summarise(rows, pay):
    pay_masks = [(ts, cst_mask(h)) for ts, h in pay]
    out = {}
    for dev in sorted({r[1] for r in rows}):
        s = [(r[0], r[2]) for r in rows if r[1] == dev]
        cells = [(ts, d["cell_voltages"]) for ts, d in s if d.get("cell_voltages")]
        hi = max(((max(c), c.index(max(c)) + 1, ts) for ts, c in cells), key=lambda x: x[0])
        lo = min(((min(c), c.index(min(c)) + 1, ts) for ts, c in cells), key=lambda x: x[0])
        dmax = max(((round(max(c) - min(c), 3), ts, max(c)) for ts, c in cells), key=lambda x: x[0])
        pv = [(d["voltage"], ts) for ts, d in s]
        cur = [(d["current"], ts) for ts, d in s]
        temps = [t for _, d in s for t in d.get("temp_values", [])]
        # average mV above the pack's lowest cell -> chronically high/low cells
        dev_sum = [0.0] * len(cells[0][1])
        for _, c in cells:
            mn = min(c)
            for i, v in enumerate(c):
                dev_sum[i] += v - mn
        avg_dev = [round(x / len(cells) * 1000, 1) for x in dev_sum]
        order = sorted(range(len(avg_dev)), key=lambda i: avg_dev[i])
        peak_c = next(c for ts, c in cells if ts == dmax[1])
        peak_order = sorted(range(len(peak_c)), key=lambda i: peak_c[i])
        if dev == "CST":
            masks, bal_src = pay_masks, "0x0A03 bytes 6-7 LE"
        else:
            masks = [(ts, d.get("balancer") or 0) for ts, d in s]
            bal_src = "aiobmsble 'balancer' (bit order unverified)"
        first = next(((ts, m) for ts, m in masks if m), None)
        first_v = None
        if first:
            prior = [c for ts, c in cells if ts <= first[0]] or [cells[0][1]]
            first_v = max(prior[-1])
        union = 0
        for _, m in masks:
            union |= m
        # Bit-order check: mean mV above pack min of flagged cells, normal vs mirrored (bit0 = cell 16)
        cell_at = dict(cells)
        norm, mirr = [], []
        for ts, m in masks:
            c = cell_at.get(ts)
            if not m or not c:
                continue
            mn, n = min(c), len(c)
            norm += [(c[i - 1] - mn) * 1000 for i in bits(m) if i <= n]
            mirr += [(c[n - i] - mn) * 1000 for i in bits(m) if i <= n]
        bit_check = {"samples": len([1 for _, m in masks if m]),
                     "normal_mv": round(sum(norm) / len(norm), 1) if norm else None,
                     "mirrored_mv": round(sum(mirr) / len(mirr), 1) if mirr else None}
        out[dev] = {
            "samples": len(s),
            "pack_v_min": min(pv), "pack_v_max": max(pv),
            "cell_max": {"v": hi[0], "cell": hi[1], "time": hi[2]},
            "cell_min": {"v": lo[0], "cell": lo[1], "time": lo[2]},
            "delta_max_mv": {"mv": round(dmax[0] * 1000), "time": dmax[1], "at_max_cell_v": dmax[2]},
            "delta_end_mv": round((max(cells[-1][1]) - min(cells[-1][1])) * 1000),
            "current_max_chg": max(cur), "current_max_dchg": min(cur),
            "soc": [min(d.get("battery_level", 0) for _, d in s), max(d.get("battery_level", 0) for _, d in s)],
            "temp_range": [min(temps), max(temps)] if temps else None,
            "cycles": s[-1][1].get("cycles"),
            "avg_dev_above_min_mv": avg_dev,
            "highest_cells": [i + 1 for i in order[::-1][:6]],
            "lowest_cells": [i + 1 for i in order[:6]],
            "peak_cell_voltages": peak_c,
            "peak_highest": [[i + 1, peak_c[i]] for i in peak_order[::-1][:6]],
            "peak_lowest": [[i + 1, peak_c[i]] for i in peak_order[:6]],
            "balancer_source": bal_src,
            "balance_first": {"time": first[0], "cells": bits(first[1]), "max_cell_v": first_v} if first else None,
            "balance_cells_ever": bits(union),
            "balance_bit_check": bit_check,
        }
    return out


def to_md(entry):
    t = lambda x: x[11:19]
    L = [f"## {entry['log']}", "", f"Window: {entry['start']} → {entry['end']}  ", f"Settings: {entry['settings'] or '-'}  "]
    if entry.get("note"):
        L.append(f"Note: {entry['note']}  ")
    L += ["", "| Pack | Pack V min–max | Cell max (#cell @time) | Cell min (#cell @time) | Max Δ mV (@time, max cell V) | End Δ mV | Max chg / dchg A | SOC % | Temp °C | Cycles |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for dev, p in entry["packs"].items():
        L.append(
            f"| {dev} | {p['pack_v_min'][0]:.2f}–{p['pack_v_max'][0]:.2f} "
            f"| {p['cell_max']['v']:.3f} (#{p['cell_max']['cell']} @{t(p['cell_max']['time'])}) "
            f"| {p['cell_min']['v']:.3f} (#{p['cell_min']['cell']} @{t(p['cell_min']['time'])}) "
            f"| {p['delta_max_mv']['mv']} (@{t(p['delta_max_mv']['time'])}, {p['delta_max_mv']['at_max_cell_v']:.3f}) "
            f"| {p['delta_end_mv']} | {p['current_max_chg'][0]} / {p['current_max_dchg'][0]} "
            f"| {p['soc'][0]}–{p['soc'][1]} | {p['temp_range'][0]}–{p['temp_range'][1]} | {p['cycles']} |")
    L += ["", "Top 6 cells — *Avg*: mean mV above the pack's lowest cell over the whole capture (chronic high/low). "
          "*At peak Δ*: cell voltages at the moment of max Δ.", "",
          "| Pack | Avg highest (mV) | Avg lowest (mV) | At peak Δ highest (V) | At peak Δ lowest (V) |",
          "|---|---|---|---|---|"]
    for dev, p in entry["packs"].items():
        ad = p["avg_dev_above_min_mv"]
        hc = ", ".join(f"#{c} {ad[c-1]}" for c in p["highest_cells"])
        lc = ", ".join(f"#{c} {ad[c-1]}" for c in p["lowest_cells"])
        ph = ", ".join(f"#{c} {v:.3f}" for c, v in p["peak_highest"])
        pl = ", ".join(f"#{c} {v:.3f}" for c, v in p["peak_lowest"])
        L.append(f"| {dev} | {hc} | {lc} | {ph} | {pl} |")
    L += ["", "Bit-order check: avg mV above pack min of flagged cells, reading bit0 = cell 1 (normal) vs bit0 = cell 16 (mirrored). "
          "The mapping with the higher value is the real one (balanced cells should be the high ones).", "",
          "| Pack | Balance start (cells, max cell V) | Cells ever balanced (bit0 = cell 1) | Bit check normal / mirrored mV | Errors |",
          "|---|---|---|---|---|"]
    for dev, p in entry["packs"].items():
        bf = p["balance_first"]
        bfs = f"{t(bf['time'])} {bf['cells']} @{bf['max_cell_v']:.3f}" if bf else "never"
        bc = p.get("balance_bit_check") or {}
        bcs = f"{bc.get('normal_mv')} / {bc.get('mirrored_mv')} (n={bc.get('samples')})" if bc.get("samples") else "-"
        L.append(f"| {dev} | {bfs} | {p['balance_cells_ever'] or '-'} | {bcs} | {entry['errors'].get(dev, 0)} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--settings", default="")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    rows, pay, errs = parse(a.log)
    if not rows:
        raise SystemExit("no 'BMS data sample' lines found - is debug logging enabled?")
    start, end = rows[0][0], rows[-1][0]
    errs = collections.Counter(dev for ts, dev in errs if start <= ts <= end)
    entry = {
        "log": os.path.basename(a.log), "start": start, "end": end,
        "settings": a.settings, "note": a.note, "errors": dict(errs), "packs": summarise(rows, pay),
    }
    hj = os.path.join(HERE, "history.json")
    hist = json.load(open(hj, encoding="utf-8")) if os.path.exists(hj) else []
    hist = [h for h in hist if h["log"] != entry["log"]] + [entry]
    hist.sort(key=lambda h: h["start"], reverse=True)
    json.dump(hist, open(hj, "w", encoding="utf-8"), indent=1)
    md = "# BMS debug log history\n\nGenerated by `bms_log_summary.py`. Newest first. Cell numbers are 1-based.\n\n"
    md += "\n".join(to_md(h) for h in hist)
    open(os.path.join(HERE, "HISTORY.md"), "w", encoding="utf-8").write(md)
    print(to_md(entry))


if __name__ == "__main__":
    main()
