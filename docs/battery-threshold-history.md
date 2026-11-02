# Battery Threshold History

Tracks SOC (%) threshold changes for A/C automations as the solar battery bank capacity changes over time. Kept separately from the README so historical values aren't lost when thresholds are re-tuned again in the future.

## Capacity History

| Date | Bank Capacity | Notes |
|---|---|---|
| — | 300 Ah | 3× 100Ah packs (M1, S1, S2) |
| 2026-09 | 624 Ah | Added a 4th pack (324Ah, PaceEX BMS) |

## Threshold Formula (300Ah → 624Ah change)

New thresholds were derived by picking clean **remaining-Ah-to-full** checkpoints in 50Ah increments (counting backward from each automation's last time-of-day checkpoint), then converting to % via:

```
% = (Capacity_Ah - Remaining_Ah) / Capacity_Ah × 100
```

### A/C 3F Power Meter - Auto Turn Off
(turns off if SOC falls *below* threshold)

| Time | Old % (300Ah) | Old Remaining Ah | New % (624Ah) | New Remaining Ah |
|---|---|---|---|---|
| 9:00 AM | 30% | 210 Ah | 52% | 300 Ah |
| 10:00 AM | 45% | 165 Ah | 60% | 250 Ah |
| 11:00 AM | 60% | 120 Ah | 68% | 200 Ah |
| 12:00 PM | 70% | 90 Ah | 76% | 150 Ah |
| 1:00 PM | 80% | 60 Ah | 84% | 100 Ah |
| 2:00 PM | 90% | 30 Ah | 92% | 50 Ah |

### A/C 3F Power Meter - Auto Turn On
(turns on if SOC rises *above* threshold)

| Time | Old % (300Ah) | Old Remaining Ah | New % (624Ah) | New Remaining Ah |
|---|---|---|---|---|
| 10:00 AM | 45% | 165 Ah | 68% | 200 Ah |
| 11:00 AM | 60% | 120 Ah | 76% | 150 Ah |
| 12:00 PM | 75% | 75 Ah | 84% | 100 Ah |
| 1:00 PM | 90% | 30 Ah | 92% | 50 Ah |

> "Remaining Ah" = Ah still needed to reach 100% at that capacity.

### Other thresholds changed at the same time

| Automation | Old | New |
|---|---|---|
| A/C Night ON Sequence - 8PM | above 75% | above 65% (user-set, not formula-derived) |
| Daily SOC Check | above 98.9% | above 95% |

### Deferred (not yet recalibrated)

| Automation | Current threshold | Notes |
|---|---|---|
| A/C Night OFF Sequence - SOC | below 15% / 20% | Manual tuning planned after a week of observation |

## 2026-11-02 — Manual tuning

| Automation | Old | New |
|---|---|---|
| A/C Night ON Sequence - 8PM | above 65% | above 75% |
| Daily SOC Check (battery full) | above 95% for 15 min | above 98% for 5 min |
| A/C Afternoon OFF Sequence | below 98% | below 97% |
| A/C Set Target Temp +1C (non-SOC) | 5 h before sunrise | 4 h before sunrise |
