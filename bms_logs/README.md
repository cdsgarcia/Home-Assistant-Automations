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
- `python bms_logs\bms_log_summary.py --rebuild` regenerates `HISTORY.md` and `full_charge_report.xlsx` from `history.json` without a log (use after changing the table layout).
- `full_charge_report.xlsx` lists every day on which a pack reached 100% SOC, newest first. It has a Summary sheet (date, charge setting, flags such as OVP trips, and notes) plus one sheet per pack (CST, M1, S1, S2) showing: max Δ while the top cell is ≥ 3.40 V, balancing minutes, charge current at the peak, highest and 2nd-highest cell (number and V), the gap between them, the lowest cell at the same moment, and the peak time. OVP trips are highlighted. It is rebuilt automatically every time a log is processed (`full_charge_report.py`), so a new day always lands on top; column widths set in Excel are kept (matched by header). Close the file in Excel before processing a log, otherwise the save fails. Blank Bal min = balancing not tracked yet (before 2026-11-07).
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
- Days that didn't reach full (low sun): from the *Daily charge peak* table, the peak SOC / Ah, top cell and the days since the last full charge. Below about 3.40 V no balancing happens and the SOC drifts, so after a run of such days compare the Ah jump at the next reset and the top-of-charge Δ with the previous full day
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

**Better** = smaller Δ at the same top-cell voltage, lower S2 #15 peak, CST #15/#16 closer to mean. **Worse** = rising Δ, new runner cells, or S2 #15 tripping at the current charge voltage (56.4V since 2026-11-17).

## Settings changes (Gentai ES-UP, via `jbd_config`)

Original values were read from all three packs over BLE before any change. To revert, set them back with `jbd_config.esup_set` or the HA entities.

| Date | Pack(s) | Setting | Original | New | Result |
|---|---|---|---|---|---|
| 2026-11-07 ~11:00 | M1, S1, S2 | Full charge voltage `FullAdjustV` (`0x1C0C`) | 56.80 V | 56.00 V | First 100% SOC reset on 2026-11-07 16:09 (all packs) |
| 2026-11-07 ~11:00 | M1, S1, S2 | Full charge current `FullAdjustC` (`0x1C0E`) | 15.00 A | 2.00 A | Same |
| 2026-11-07 11:07 | S2 only | Balance mode (`0x1C7C`) | 0 = charge | 1 = static | Balances while charging and at rest, stops on discharge (34.7 min on 2026-11-07 vs 17.5 min S1) |
| 2026-11-14 14:17 | Inverter (HMI) | Max charge voltage | 56.4 V | 56.8 V | Step 1 of 0.4 V increments (ceiling 57.2 V, see 11-06 OVP test). Changed after the 11-14 full charge (S2 max Δ 19 mV, #15 peak 3.553 V). Projected S2 #15 peak ~3.60 V. At 14:21 (packs full, ~0 A): pack ~56.9–57.0 V, S2 #15 3.578, S1 #13 3.567, M1 #12 3.564, CST #8 3.568 |
| 2026-11-14 14:24 | Inverter (HMI) | Max charge voltage | 56.8 V | 57.2 V | Step 2, set 7 min after step 1 (no multi-day check at 56.8 V). This is the ceiling: highest setting without OVP trips in the 11-06 test (M1/S1 3.619, CST 3.607). Expect CST cell-OV warning (3.60 V alarm) |
| 2026-11-17 16:02 | Inverter (HMI) | Max charge voltage | 57.2 V | 57.6 V | Step 3, set with packs already full (~0 A). Above the 11-06 ceiling (57.6 V tripped M1/S1/CST then, S2 at 56.8 V). At 16:08: pack 57.7–57.8 V, peaks CST #8 3.629, M1 #12 3.626, S1 #13 3.628, S2 #15 3.625; Δ CST 35, M1 21, S1 21, **S2 12 mV**. No faults. Revert to 57.2 V if any pack trips cell OVP (3.65 V) |
| 2026-11-17 16:00:57 | S1 only | Balance mode (`0x1C7C`, set via ES-UP, verified) | 0 = charge | 1 = static | Same as S2 since 11-07. M1 stays in charge mode as the reference |
| 2026-11-17 16:19:11 | M1 only | Balance mode (`0x1C7C`, set via ES-UP, verified) | 0 = charge | 1 = static | All three Gentai packs now static (no charge-mode reference left; packs well matched: Max Δ 11-17 M1 21, S1 21, S2 12 mV) |
| 2026-11-17 ~16:25 | Inverter (HMI) | Max charge voltage | 57.6 V | 56.4 V | Back to long-term setting to reduce cell stress: at 57.6 V all peaks ~3.63 V (CST above its 3.60 V alarm). 11-17 peaks: CST #8 3.629, M1 #12 3.626, S1 #13 3.628, S2 #15 3.625; Max Δ CST 40, M1 21, S1 21, S2 12 mV. Compare future Max Δ with the 56.4 V days (11-07 to 11-10). Optional monthly top-check day at 57.2 V |
| 2026-11-26 08:16 | S2 only | Discharge current gain (`0x3800`+4, written at `0x3802` in DV/DP mode, verified) | 10710 | 10320 | S2 SOC drift: full-charge corrections 11-10 to 11-17 were 5.7% of discharged Ah vs M1 2.6%, S1 1.6%; S2 read ~4% more than M1/S1 when discharging but matched them when charging. Charge gain 10714 unchanged (M1 10585/10567, S1 10593/10563). Likely a zero offset (~0.1 A), so heavy discharge may now read 2–3% low. Revert: write 10710. Check the next full-charge correction (expect ≤ M1's) |
| 2026-11-26 08:30 | S2 only | Charge current gain (`0x3800`+0, written at `0x3800` in DV/DP mode, verified) | 10714 | 10320 | User request to match the other packs, whose charge and discharge gains are nearly equal. Expect S2 to now read ~3.7% low on charge, so watch for the drift to come back in the other direction (SOC under-reads charge and hits 100% late, giving a positive full-charge correction). Revert: write 10714 |
| 2026-11-26 08:48 | S2 only | Charge gain 10320 → **10714** (restored) and discharge gain 10320 → **10250**, written via pinned address 3 and verified | 10320 / 10320 | 10714 / 10250 | Full analysis of BMS counters 11-09 to 11-25 (about 590 Ah charge and 640 Ah discharge per pack, split by current direction). S2 vs the M1/S1 mean: charge ×1.001 (per-period range 0.988–1.018), discharge ×1.045 (range 1.034–1.051). The discharge excess is about +0.1 A whatever the current (an offset); 10710 ÷ 1.045 ≈ 10250. Live check: after charge 10320, S2 read ×0.984 of M1/S1 on charge (under-read), so it was restored. The inverter current (1 A steps, +1.3 to +1.8 A offset) can't resolve S2 on its own. Check: S2 discharge Ah ≈ M1/S1 and its next full-charge correction ≈ M1/S1's. Original values: 10714 / 10710 |

## Current Gentai Current Gains — 2026-11-26

Read from the live HA gain entities at 09:00 (entity refresh times 08:45–08:50). Original values are the recorded pre-calibration gains.

| Pack | Original charge | Current charge | Original discharge | Current discharge |
|---|---:|---:|---:|---:|
| M1 | 10585 | 10585 | 10567 | 10567 |
| S1 | 10593 | 10593 | 10563 | 10563 |
| S2 | 10714 | 10714 | 10710 | 10250 |

Unchanged on all three packs: balance start 3400 mV, balance delta 8 mV, Cell Full 3600 mV, sleep 3000/2880 mV, RS485 protocol 6, CAN 0. Inverter: charge 56.4 V (since 2026-11-17 ~16:25), stop charge 2 A, recharge 53.6 V. Balance mode: M1, S1 and S2 static.
