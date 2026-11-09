# BMS debug log history

Running record of BMS_BLE debug-log captures (CST + Gentai M1/S1/S2), so each new capture can be compared with earlier ones.

## Add a new capture

Daily files come from the [`BMS Debug Log - Start on Boot`](../automations/bms_debug_log_start_on_boot.yaml) automation: `/config/bms_logs/bms_ble_YYYY-MM-DD.log.gz` on the HA host. The script reads `.gz` files directly, and each daily file becomes one capture.

```powershell
python bms_logs\bms_log_summary.py "<path>\bms_ble_2026-11-08.log.gz" `
  --settings "Inverter HMI 56.0V, stop chg 2A, recharge 54.0V, no BMS comm; balance 3.400V/8mV" `
  --note "What changed / why this capture"
```

- `HISTORY.md` holds the readable tables, newest first. `history.json` holds the full data, including per-cell average deviation.
- Running the script again on the same log file replaces its entry instead of adding a duplicate.
- `python bms_logs\bms_log_summary.py --rebuild` regenerates `HISTORY.md` from `history.json` without a log (use after changing the table layout).
- The top-cells table includes the gap (mV) between the 1st and 2nd highest / lowest cell at peak Δ. A large highest-cell gap means a single runaway cell.
- The **top-of-charge** table records, for the first time each pack's highest cell reaches 3.40 / 3.45 / 3.50 / 3.55 / 3.58 / 3.60 / 3.62 / 3.65 V, the charge current, the cell, the gap to the 2nd-highest cell and the pack Δ, plus the minutes and mean/max current from 3.40 V to the peak. Charge current lifts the highest cell (I × R), so only compare gaps between captures at similar current. `--rebuild` backfills this from `samples_<date>.csv` for older captures.
- `samples_<date>.csv` holds every parsed sample, one file per day: time, pack, pack V, current, SOC, remaining Ah, power, cycles, problem code, balance mask, all 16 cells, every temperature sensor (`temps`, `;`-separated; CST's 4 cell sensors, Gentai's 6) and BLE errors as rows with `event=ble_error`. With this file the raw logs can be deleted and the data re-analysed later. Re-running a log merges into the file without duplicates. Files before 2026-11-09 lack `remaining_ah`, `power`, `temps` and `event`.
- The **balancing** table shows, per pack, the minutes with any balance bit set (split into charge > +0.2 A / rest / discharge < −0.2 A), the min/mean/max pack current while balancing, and the minutes per cell. Below it, every balancing start/stop is listed with pack V, current, highest cell and Δ. Gentai cells in this section use the mirrored bit order (bit0 = cell 16). The 2026-11-07 captures show the highest cells balancing under that order (patman15/aiobmsble#328).
- CST balance cells come from aiobmsble's `balancer` field (0.33.0+ reads `0x0A03` natively: bytes 6–7, little-endian, bit0 = cell 1). Older captures used the patched `0A03 payload` debug lines. Gentai balance bits come from aiobmsble's `balancer` field, and their bit order is **unverified** (it may be mirrored).

## Weekly review checklist (every pack: M1, S1, S2, CST)

Report each item for **all four packs side by side**, not just a pack under trial. A trial pack (e.g. S2 static balancing) is judged against the others.

- Peak highest cell (#, V) and peak Δ / gap to the 2nd-highest cell, at similar top-cell voltage and current
- Top-of-charge crossings (3.40 V → peak): time, current, cell
- Balancing: minutes (charge / rest / discharge), current while balancing, max temp while balancing, minutes per cell, start/stop events
- Full-charge SOC resets: time, pack V, current, Ah before → after, 99↔100 flips, release time and Ah
- Temperature range (all sensors) and any pack running hotter than the others
- Charge current share between packs, and any pack-to-pack current (one discharging while another charges)
- Lowest cell at the bottom of discharge
- BLE errors per pack, BMS alarm/protection codes, missing samples

## Baseline — 2026-11-02 (compare future captures against this)

Data: `samples_2026-11-02.csv` (5 logs, 11:40–15:26). Compare at the **same top-cell voltage** — spread grows on the charge knee, so mid-SOC readings hide imbalance.

| Pack | Cycles | Δ at rest ~3.32V | Δ at rest, top cell ~3.545V | Runner cell(s) (mV above pack min at peak) | Low cell(s) |
|---|---|---|---|---|---|
| CST | 16 | 1 mV | 40–43 mV | #8 +55, #5 +52 | #16, #15 (never bled) |
| M1 | 265 | 3 mV | — | #12 +42 | #3 |
| S1 | 294 | 3 mV | — | #13 +45, #16 | #7 |
| S2 | 308 | 5 mV | 66 mV (@3.592V) | **#15 +115 (3.649V, OVP trip at ≥56.8V)**, #13 +66, #10 +57, #14 +53 | #1 |

Notes:
- At 56.4V charge, S2 #15 peaks 3.60–3.61V (~40mV under OVP). OVP trip 3.65V, release ~3.38V.
- CST (Pace) bleeds cells 1–14 together, leaving only #15/#16; ~3 mV improvement per 1.5 h at rest. Balancer too weak to show as a voltage drop on 324Ah.
- Gentai balancing was charge-only at baseline (FW 0x0A ignores register 0x2D; balance mode lives in ES-UP `0x1C7C`, see Settings changes); start 3.400V, delta 8mV.

**Better** = smaller Δ at the same top-cell voltage, lower S2 #15 peak, CST #15/#16 closer to mean. **Worse** = rising Δ, new runner cells, or S2 #15 tripping at 56.4V.

## Settings changes (Gentai ES-UP, via `jbd_config`)

Original values were read from all three packs over BLE before any change. To revert, set them back with `jbd_config.esup_set` or the HA entities.

| Date | Pack(s) | Setting | Original | New | Result |
|---|---|---|---|---|---|
| 2026-11-07 ~11:00 | M1, S1, S2 | Full charge voltage `FullAdjustV` (`0x1C0C`) | 56.80 V | 56.00 V | First 100% SOC reset on 2026-11-07 16:09 (all packs) |
| 2026-11-07 ~11:00 | M1, S1, S2 | Full charge current `FullAdjustC` (`0x1C0E`) | 15.00 A | 2.00 A | Same |
| 2026-11-07 11:07 | S2 only | Balance mode (`0x1C7C`) | 0 = charge | 1 = static | Balances while charging and at rest, stops on discharge (34.7 min on 2026-11-07 vs 17.5 min S1) |

Unchanged on all three packs: balance start 3400 mV, balance delta 8 mV, Cell Full 3600 mV, sleep 3000/2880 mV, RS485 protocol 6, CAN 0. Inverter: charge 56.4 V, stop charge 2 A, recharge 53.6 V.
