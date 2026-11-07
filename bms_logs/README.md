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
- `samples_<date>.csv` holds every parsed sample (all packs, all 16 cells, current, SOC, temp, balance mask) so the raw logs can be deleted and the data re-analysed later.
- CST balance cells are decoded from the Pace `0x0A03` payload (bytes 6–7, little-endian, bit0 = cell 1). Gentai balance bits come from aiobmsble's `balancer` field, and their bit order is **unverified** (it may be mirrored).

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
- Gentai balancing is charge-only (FW 0x0A ignores register 0x2D); start 3.400V, delta 8mV.

**Better** = smaller Δ at the same top-cell voltage, lower S2 #15 peak, CST #15/#16 closer to mean. **Worse** = rising Δ, new runner cells, or S2 #15 tripping at 56.4V.
