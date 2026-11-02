# BMS debug log history

Running record of BMS_BLE debug-log captures (CST + Gentai M1/S1/S2), so each new capture can be compared with earlier ones.

## Add a new capture

```powershell
python bms_logs\bms_log_summary.py "<path>\home-assistant_bms_ble_<timestamp>.log" `
  --settings "Inverter HMI 56.0V, stop chg 2A, recharge 54.0V, no BMS comm; balance 3.400V/8mV" `
  --note "What changed / why this capture"
```

- `HISTORY.md` holds the readable tables, newest first. `history.json` holds the full data, including per-cell average deviation.
- Running the script again on the same log file replaces its entry instead of adding a duplicate.
- CST balance cells are decoded from the Pace `0x0A03` payload (bytes 6–7, little-endian, bit0 = cell 1). Gentai balance bits come from aiobmsble's `balancer` field, and their bit order is **unverified** (it may be mirrored).
