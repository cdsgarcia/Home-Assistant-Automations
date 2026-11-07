"""Summarise a BMS_BLE debug log and append the result to the history files.

Usage: python bms_log_summary.py <home-assistant_bms_ble_*.log> [--settings "..."] [--note "..."]

Writes/updates (next to this script):
  history.json  - machine-readable list of capture summaries (one per log file)
  HISTORY.md    - human-readable tables, newest capture first
"""
import argparse, ast, collections, csv, datetime, gzip, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_RE = re.compile(r"(\S+ \S+) \w+ .*?\] (\S+): BMS data sample (\{.*\})\s*$")
PAY_RE = re.compile(r"(\S+ \S+) .*0A03 payload: (.*)$")


def parse(path):
    rows, pay, errs = [], [], []
    for line in (gzip.open if path.endswith(".gz") else open)(path, "rt", encoding="utf-8", errors="replace"):
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


TOC_THRESHOLDS = (3.40, 3.45, 3.50, 3.55, 3.58, 3.60, 3.62, 3.65)


def top_of_charge(samples):
    """samples: [(ts, current_A, cell_voltages)] in time order.

    Charge current lifts the highest cell (I x R), so gaps are only comparable between
    captures at similar current. For the first time the highest cell reaches each
    threshold, record the current, cell, gap to the 2nd-highest cell and pack delta.
    """
    samples = [x for x in samples if x[2]]
    if not samples:
        return None
    crossings, done = [], set()
    for k, (ts, cur, c) in enumerate(samples):
        hi, srt = max(c), sorted(c)
        for th in TOC_THRESHOLDS:
            if th not in done and hi >= th:
                done.add(th)
                crossings.append({"th": th, "time": ts, "current": cur, "cell": c.index(hi) + 1, "hi": hi,
                                  "gap_mv": round((srt[-1] - srt[-2]) * 1000),
                                  "delta_mv": round((hi - srt[0]) * 1000), "at_start": k == 0})
    if not crossings:
        return {"crossings": [], "window": None}
    t0 = crossings[0]["time"]
    i0 = next(k for k, x in enumerate(samples) if x[0] == t0)
    ipk = max(range(i0, len(samples)), key=lambda k: max(samples[k][2]))
    win = samples[i0:ipk + 1]
    curs = [x[1] for x in win]
    return {"crossings": crossings, "window": {
        "start": t0, "peak": samples[ipk][0], "peak_v": max(samples[ipk][2]),
        "minutes": round((_ts(samples[ipk][0]) - _ts(t0)) / 60, 1),
        "mean_current": round(sum(curs) / len(curs), 2), "max_current": max(curs)}}


def _ts(s):
    return datetime.datetime.fromisoformat(s).timestamp()


def balancing(samples, mirror):
    """samples: [(ts, data)] in time order; mirror: Gentai bit0 = cell 16 (aiobmsble#328).

    Each balancing sample counts until the next sample (capped at 90 s so BLE gaps don't inflate it).
    Returns minutes split by charge (> +0.2 A) / rest / discharge (< -0.2 A), per-cell minutes,
    the pack current while balancing, and every start/stop transition.
    """
    cellno = (lambda b: 16 - b) if mirror else (lambda b: b + 1)
    mins = {"charge": 0.0, "rest": 0.0, "discharge": 0.0}
    per_cell, curs, events, prev = collections.Counter(), [], [], 0
    for k, (ts, d) in enumerate(samples):
        m, c, i = d.get("balancer") or 0, d.get("cell_voltages") or [], d["current"]
        if bool(m) != bool(prev) and c:
            events.append({"time": ts, "event": "start" if m else "stop", "pack_v": d["voltage"], "current": i,
                           "hi_cell": c.index(max(c)) + 1, "hi_v": max(c),
                           "delta_mv": round((max(c) - min(c)) * 1000)})
        prev = m
        if not m or k + 1 == len(samples):
            continue
        dur = min(_ts(samples[k + 1][0]) - _ts(ts), 90) / 60
        mins["charge" if i > 0.2 else "discharge" if i < -0.2 else "rest"] += dur
        curs.append(i)
        for b in range(16):
            if m >> b & 1:
                per_cell[cellno(b)] += dur
    if not curs:
        return None
    temps = [t for ts, d in samples if d.get("balancer") for t in d.get("temp_values", [])]
    return {"minutes": {k: round(v, 1) for k, v in mins.items()}, "total_min": round(sum(mins.values()), 1),
            "current": {"min": min(curs), "mean": round(sum(curs) / len(curs), 2), "max": max(curs)},
            "temp_max": max(temps) if temps else None,
            "per_cell_min": {str(c): round(v, 1) for c, v in per_cell.most_common()},
            "cell_map": "bit0 = cell 16 (mirrored)" if mirror else "bit0 = cell 1", "events": events}


def soc_resets(samples):
    """Full-charge SOC resets: battery_level jumps to 100 by >= 2 points or cycle_charge jumps > 1 Ah.

    For each reset, also record when the BMS first reports < 100 % again (the hold release) and the
    number of 99<->100 flips in between.
    """
    out = []
    for k in range(1, len(samples)):
        (_, a), (ts, b) = samples[k - 1], samples[k]
        sa, sb = a.get("battery_level"), b.get("battery_level")
        if sb != 100 or not ((sa is not None and sb - sa >= 2) or b["cycle_charge"] - a["cycle_charge"] > 1):
            continue
        c = b.get("cell_voltages") or [0]
        r = {"time": ts, "pack_v": b["voltage"], "current": b["current"], "soc_before": sa,
             "ah_before": a["cycle_charge"], "ah_after": b["cycle_charge"], "hi_v": max(c),
             "delta_mv": round((max(c) - min(c)) * 1000), "flips": 0, "release": None}
        prev = 100
        for ts2, d in samples[k + 1:]:
            s = d.get("battery_level")
            if s is not None and s < 100 and d["current"] < -0.2:
                r["release"] = {"time": ts2, "ah": d["cycle_charge"], "soc": s}
                break
            if s != prev:
                r["flips"] += 1
            prev = s
        out.append(r)
    return out


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
        if dev == "CST" and pay_masks:
            masks, bal_src = pay_masks, "0x0A03 bytes 6-7 LE"
        elif dev == "CST":  # aiobmsble >= 0.33 decodes 0x0A03 natively into 'balancer'
            masks = [(ts, d.get("balancer") or 0) for ts, d in s]
            bal_src = "aiobmsble 'balancer' (0x0A03 bytes 6-7 LE)"
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
            "top_of_charge": top_of_charge([(ts, d["current"], d.get("cell_voltages")) for ts, d in s]),
            "balancing": balancing(s, mirror=dev != "CST"),
            "soc_resets": soc_resets(s),
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
          "*At peak Δ*: cell voltages at the moment of max Δ. "
          "*Gap*: mV between the 1st and 2nd highest / lowest cell at peak Δ (a large gap = one runaway cell).", "",
          "| Pack | Avg highest (mV) | Avg lowest (mV) | At peak Δ highest (V) | At peak Δ lowest (V) | Gap 1st–2nd highest / lowest (mV) |",
          "|---|---|---|---|---|---|"]
    for dev, p in entry["packs"].items():
        ad = p["avg_dev_above_min_mv"]
        hc = ", ".join(f"#{c} {ad[c-1]}" for c in p["highest_cells"])
        lc = ", ".join(f"#{c} {ad[c-1]}" for c in p["lowest_cells"])
        ph = ", ".join(f"#{c} {v:.3f}" for c, v in p["peak_highest"])
        pl = ", ".join(f"#{c} {v:.3f}" for c, v in p["peak_lowest"])
        gh = round((p["peak_highest"][0][1] - p["peak_highest"][1][1]) * 1000)
        gl = round((p["peak_lowest"][1][1] - p["peak_lowest"][0][1]) * 1000)
        L.append(f"| {dev} | {hc} | {lc} | {ph} | {pl} | {gh} / {gl} |")
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
    L += ["", "Top of charge — first time the highest cell reaches each threshold: "
          "`current A · #cell · gap/Δ mV` (gap = 1st−2nd highest cell). Charge current lifts the highest cell, "
          "so compare gaps between captures only at similar current. `*` = already above at capture start. "
          "Window = from the 3.40 V crossing to the peak cell.", "",
          "| Pack | " + " | ".join(f"{th:.2f} V" for th in TOC_THRESHOLDS) + " | Window (min, mean / max A, peak V) |",
          "|---|" + "---|" * len(TOC_THRESHOLDS) + "---|"]
    for dev, p in entry["packs"].items():
        toc = p.get("top_of_charge")
        if not toc:
            continue
        by = {x["th"]: x for x in toc["crossings"]}
        cols = []
        for th in TOC_THRESHOLDS:
            x = by.get(th)
            cols.append(f"{x['current']} A · #{x['cell']} · {x['gap_mv']}/{x['delta_mv']}{'*' if x['at_start'] else ''}"
                        f" @{t(x['time'])[:5]}" if x else "–")
        w = toc["window"]
        ws = f"{w['minutes']} min, {w['mean_current']} / {w['max_current']} A, {w['peak_v']:.3f}" if w else "–"
        L.append(f"| {dev} | " + " | ".join(cols) + f" | {ws} |")
    bal = {dev: p["balancing"] for dev, p in entry["packs"].items() if p.get("balancing")}
    if bal:
        L += ["", "Balancing — time with any balance bit set (charge > +0.2 A, rest, discharge < −0.2 A), "
              "pack current while balancing, and minutes per cell. Gentai cells use bit0 = cell 16 (aiobmsble#328).", "",
              "| Pack | Total min | Charge / rest / discharge min | Current min / mean / max A | Max temp °C | Minutes per cell |",
              "|---|---|---|---|---|---|"]
        for dev, b in bal.items():
            m, c = b["minutes"], b["current"]
            pc = ", ".join(f"#{k} {v}" for k, v in b["per_cell_min"].items())
            L.append(f"| {dev} | {b['total_min']} | {m['charge']} / {m['rest']} / {m['discharge']} "
                     f"| {c['min']} / {c['mean']} / {c['max']} | {b.get('temp_max', '-')} | {pc} |")
        L += ["", "Balancing start/stop events: `time event · pack V · current A · highest #cell V · Δ mV`.", ""]
        for dev, b in bal.items():
            ev = "; ".join(f"{t(e['time'])} {e['event']} · {e['pack_v']:.2f} V · {e['current']} A · "
                           f"#{e['hi_cell']} {e['hi_v']:.3f} · {e['delta_mv']}" for e in b["events"])
            L.append(f"- **{dev}**: {ev}")
    resets = [(dev, r) for dev, p in entry["packs"].items() for r in (p.get("soc_resets") or [])]
    if any("soc_resets" in p for p in entry["packs"].values()):
        L += ["", "Full-charge SOC resets — BMS jump to 100 %; *Release* = first < 100 % while discharging; "
              "*Flips* = 99↔100 changes before release.", "",
              "| Pack | Time | Pack V | Current A | SOC / Ah before → after | Highest cell V | Δ mV | Flips | Release (time, Ah) |",
              "|---|---|---|---|---|---|---|---|---|"]
        for dev, r in resets:
            rel = f"{t(r['release']['time'])}, {r['release']['ah']}" if r["release"] else "not yet"
            L.append(f"| {dev} | {t(r['time'])} | {r['pack_v']:.2f} | {r['current']} | {r['soc_before']}% / {r['ah_before']} → "
                     f"{r['ah_after']} | {r['hi_v']:.3f} | {r['delta_mv']} | {r['flips']} | {rel} |")
        if not resets:
            L.append("| – | no reset in this capture | | | | | | | |")
    return "\n".join(L) + "\n"


def csv_samples(entry):
    """Rebuild (pack -> [(ts, current, cells)]) for an entry from samples_<date>.csv."""
    path = os.path.join(HERE, f"samples_{entry['start'][:10]}.csv")
    if not os.path.exists(path):
        return {}
    out = collections.defaultdict(list)
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if entry["start"] <= r["time"] <= entry["end"]:
            out[r["pack"]].append((r["time"], float(r["current"]), [float(r[f"c{i}"]) for i in range(1, 17)]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?")
    ap.add_argument("--settings", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--rebuild", action="store_true", help="only regenerate HISTORY.md from history.json")
    a = ap.parse_args()
    hj = os.path.join(HERE, "history.json")
    if a.rebuild:
        hist = json.load(open(hj, encoding="utf-8"))
        for h in hist:  # backfill top-of-charge for captures summarised before it existed
            smp = csv_samples(h)
            for dev, p in h["packs"].items():
                if "top_of_charge" not in p and smp.get(dev):
                    p["top_of_charge"] = top_of_charge(smp[dev])
        json.dump(hist, open(hj, "w", encoding="utf-8"), indent=1)
        write_md(hist)
        return
    if not a.log:
        ap.error("log is required unless --rebuild is given")
    rows, pay, errs = parse(a.log)
    if not rows:
        raise SystemExit("no 'BMS data sample' lines found - is debug logging enabled?")
    start, end = rows[0][0], rows[-1][0]
    errs = collections.Counter(dev for ts, dev in errs if start <= ts <= end)
    entry = {
        "log": os.path.basename(a.log), "start": start, "end": end,
        "settings": a.settings, "note": a.note, "errors": dict(errs), "packs": summarise(rows, pay),
    }
    hist = json.load(open(hj, encoding="utf-8")) if os.path.exists(hj) else []
    hist = [h for h in hist if h["log"] != entry["log"]] + [entry]
    hist.sort(key=lambda h: h["start"], reverse=True)
    json.dump(hist, open(hj, "w", encoding="utf-8"), indent=1)
    write_md(hist)
    print(to_md(entry))


def write_md(hist):
    md = "# BMS debug log history\n\nGenerated by `bms_log_summary.py`. Newest first. Cell numbers are 1-based.\n\n"
    md += "\n".join(to_md(h) for h in hist)
    open(os.path.join(HERE, "HISTORY.md"), "w", encoding="utf-8").write(md)


if __name__ == "__main__":
    main()
