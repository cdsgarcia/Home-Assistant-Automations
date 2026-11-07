# Home Assistant Automations

<a href="#support"><img src="https://cdn.buymeacoffee.com/buttons/default-orange.png" alt="Buy Me A Coffee" height="28" width="120"></a>

A collection of Home Assistant automations for a solar-powered home, focused on intelligent A/C energy management, battery state-of-charge (SOC) optimisation, and auxiliary device control.

---

## Overview

These automations manage three main areas:

| Area | Description |
|------|-------------|
| **A/C Energy Management** | Automatically turns the 3rd-floor A/C on/off based on solar battery SOC and time of day |
| **Auxiliary Devices** | Controls the roof deck light and portable power station indicator light |

---

## Devices & Entities Referenced

| Device / Entity | Role |
|----------------|------|
| `sensor.battery_soc_mean` | Capacity-weighted mean solar battery SOC across all packs (%) |
| `climate.ac` | 3rd-floor A/C unit (IR/WiFi climate entity) |
| A/C 3F Power Meter (`device_id: 73be0b81…`) | Smart plug monitoring the A/C circuit |
| `sensor.a_c_power_meter_power_filtered_5m` | Filtered real-time power draw of the A/C — 5 min average (W) |
| `sensor.a_c_power_meter_power_filtered_10m` | Filtered real-time power draw of the A/C — 10 min average (W) |
| `sensor.a_c_power_meter_power_filtered_30m` | Filtered real-time power draw of the A/C — 30 min average (W) |
| `input_boolean.battery_reached_100_today` | Flag: battery reached full charge today |
| `input_datetime.a_c_last_adjusted` | Timestamp of last A/C temperature adjustment (cooldown tracking) |
| EF-R30241 (`device_id: bbdfa75a…`) | Portable power station |
| Roof Deck Light (`device_id: 8510f0c0…`) | Outdoor roof deck switch |

---

## Configuration (`configuration.yaml`)

The `configuration.yaml` file extends HA with custom sensors used across automations.

### Template Sensors

| Sensor | Description |
|--------|-------------|
| `sensor.grid_l1_power_signed` | Converts raw unsigned int to signed watts (import = positive, export = negative) |
| `sensor.today_battery_charge_kwatt_hour` | Battery energy charged today in kWh (Ah × V ÷ 1000) |
| `sensor.today_battery_discharge_kwatt_hour` | Battery energy discharged today in kWh (Ah × V ÷ 1000) |
| `sensor.total_battery_power_absolute` | Absolute value of battery power — always positive (W) |
| `sensor.pv_power_kw` | PV solar power in kW (1 decimal) |
| `sensor.total_battery_power_kw` | Battery power in kW |
| `sensor.total_battery_power_absolute_kw` | Absolute battery power in kW |
| `sensor.battery_reached_full_today` | Human-readable `YES`/`NO` mirror of `input_boolean.battery_reached_100_today` |
| `sensor.battery_soc_mean` | Capacity-weighted mean SOC across all battery packs (rated Ah). A pack dropout under 5 min holds the last value; a longer outage reports `unavailable` rather than a skewed average |

### Statistics Filter Sensors

Rolling mean of A/C power draw sourced from `sensor.a_c_1f_power_meter_power`:

| Sensor | Window | Use |
|--------|--------|-----|
| `sensor.a_c_power_meter_power_filtered_5m` | 5 min | Used by automations |
| `sensor.a_c_power_meter_power_filtered_10m` | 10 min | Available for tuning |
| `sensor.a_c_power_meter_power_filtered_30m` | 30 min | Available for tuning |

---

## Automations

### ☀️ Solar Battery / SOC Management

#### [`Daily SOC Check`](automations/daily_soc_check.yaml)
Tracks whether the battery reached full charge (>97%, sustained for 5 minutes) during the day.

- **On battery full** (daytime only): sets `input_boolean.battery_reached_100_today` to `on` and re-enables `automation.roof_deck_light` (if disabled)
- **On sunrise**: resets the flag to `off` and re-enables the *Auto Turn On* automation (if disabled)
- **On sunset** (if battery never reached 100% today): turns off the A/C power meter switch and disables the *Auto Turn On* automation

> All enable/disable actions use idempotent guards — they only act if the automation is not already in the desired state.

---

### ❄️ A/C 3F Power Meter — Circuit Switch Control

These automations manage the smart plug that supplies the A/C.

#### [`A/C 3F Power Meter - Auto Turn On SOC`](automations/ac_3f_power_meter_auto_turn_on_soc.yaml)
When `input_boolean.battery_reached_100_today` turns `on`:
- Re-enables the *Auto Turn On* automation (if `input_boolean.auto_turn_on_managed` is `on`)
- Immediately turns on the A/C power meter switch (if currently off)

#### [`A/C 3F Power Meter - Auto Turn On`](automations/ac_3f_power_meter_auto_turn_on.yaml)
Turns the A/C power meter switch **on** at progressive time windows, gated by battery SOC:

| Time | Min Battery SOC |
|------|----------------|
| 10:00 | > 68% |
| 11:00 | > 76% |
| 12:00 | > 84% |
| 13:00 | > 92% |

Only fires if the switch is currently off.

#### [`A/C 3F Power Meter - Auto Turn Off`](automations/ac_3f_power_meter_auto_turn_off.yaml)
Turns the A/C power meter switch **off** at progressive time windows if the battery has dropped below a threshold:

| Time | Max Battery SOC |
|------|----------------|
| 09:00 | < 52% |
| 10:00 | < 60% |
| 11:00 | < 68% |
| 12:00 | < 76% |
| 13:00 | < 84% |
| 14:00 | < 92% |

Only fires if the switch is currently on.

#### [`A/C 3F Power Meter - 3.5 kWh Limit`](automations/ac_3f_power_meter_35_kwh_limit.yaml)
Turns the A/C power meter switch **off** if total energy consumption exceeds **3.5 kWh** (and has been above it for 1 minute). Active between 15:00 and 09:00 only.

#### [`A/C 3F Power Meter - Total Energy Reset`](automations/ac_3f_power_meter_total_energy_reset.yaml)
Resets the power meter's energy counter daily at **15:00** by pressing the data-reset button.

#### [`A/C 3F Power Meter - Data Refresh Interval`](automations/ac_3f_power_meter_data_refresh_interval.yaml)
Enforces a fixed data refresh interval of **30 seconds** on HA start and whenever the interval drifts above or below 30.

#### [`A/C 3F Power Meter - Display Off`](automations/ac_3f_power_meter_display_off.yaml)
When the refresh interval entity changes, waits 10 seconds then selects the last display option (turns off the meter's physical display).

---

### 🌙 A/C Night Sequences

#### [`A/C Night ON Sequence - 8PM`](automations/ac_night_on_sequence_8pm.yaml)
At **20:00**, if battery SOC > 75% and A/C is not already in cool mode:
1. Sets HVAC to `fan_only`
2. Waits 2 minutes
3. Sets HVAC to `cool`
4. Waits 10 seconds
5. Sets target temperature to **26°C**

#### [`A/C Night OFF Sequence - SOC`](automations/ac_night_off_sequence_soc.yaml)
Gracefully shuts down the A/C when the battery runs low at night:
- **Evening (21:00–02:00)**: triggers when battery drops below **20%**
- **Early morning (21:00–07:00)**: triggers when battery drops below **15%**

Sequence: set temp to 30°C → 10 s delay → `fan_only` → 15 min delay → `off`

#### [`A/C Set Target Temp +1C`](automations/ac_set_target_temp_plus_1c.yaml)
At **4 hours before sunrise**, if the A/C is not off, increases the current target temperature by **1°C**.

---

#### [`A/C Night OFF Sequence - Sunrise`](automations/ac_night_off_sequence_sunrise.yaml)
At sunrise, if the A/C is still running:

Sequence: set temp to 30°C → 10 s delay → `fan_only` → 15 min delay → `off`

---

### 🌅 A/C Afternoon Sequence

#### [`A/C Afternoon OFF Sequence`](automations/ac_afternoon_off_sequence.yaml)
Between **15:00–18:00**, if the solar battery drops below 97% SOC for 1 minute and the A/C is not off:

Sequence: set temp to 30°C → 10 s delay → `fan_only` → 10 min delay → `off`

---

### 🌡️ A/C Smart Temperature Control

> **Note:** The `A/C Auto Set` automation has been disabled. The YAML is retained in [`automations/ac_auto_set.yaml`](automations/ac_auto_set.yaml) for reference/backup.

---

### 💡 Auxiliary Devices

#### [`Roof Deck Light`](automations/roof_deck_light.yaml)
- Turns **on** 20 minutes after sunset
- Turns **off** 20 minutes before sunrise

> Uses idempotent guards — only acts if the light is not already in the desired state.

#### [`EF-R30241 Light`](automations/ef_r30241_light.yaml)
Controls the indicator light mode on the portable power station based on charge state.

| State | Light Mode |
|-------|-----------|
| Plugged in (charging) | Off |
| Unplugged, battery < 21% | SOS |
| Unplugged, battery 21–50% | Bright |
| Unplugged, battery > 50% | Dim |

**Behaviour while unplugged:**
- Starts at **Dim** when first unplugged (battery typically above 50%)
- Automatically switches to **Bright** when battery drops below 50%
- Automatically switches to **SOS** when battery drops below 21%
- When plugged back in, light turns **Off** immediately regardless of battery level

### 🔔 Notifications

#### [`CYD-E713B0 Crash Alert`](automations/cyd_e713b0_crash_alert.yaml)
Sends a mobile notification when the CYD-E713B0 device resets due to a real fault. Filters out routine reboots (OTA, API, power-on) and only alerts on: `panic`, `exception`, `brownout`, `watchdog`, `fault`, `glitch`, `lock up`, or `efuse error`.

---

## Tools

### [`custom_components/jbd_config`](custom_components/jbd_config) — JBD BMS Config integration
Shows and edits the JBD (Gentai) BMS settings in Home Assistant over BLE. The entities appear on each pack's existing BMS_BLE device page:

| Entity | Purpose |
|---|---|
| Cell full voltage (mV) | Register `0x12`: SOC resets to 100% when any cell reaches this voltage |
| Balance start voltage (mV) | Register `0x2A` |
| Balance delta (mV) | Register `0x2B` |
| Read BMS settings (button) | Refreshes the values above from the BMS |
| Full charge voltage (ES-UP) (V) | `FullAdjustV`, ES-UP register `0x1C0C`: the real 100% SOC reset voltage on UP16S firmware 12.x |
| Full charge current (ES-UP) (A) | `FullAdjustC`, `0x1C0E`: the charge current must have fallen to this for the reset |
| Read ES-UP settings (button) | Refreshes the ES-UP values |
| Balance mode (ES-UP) (select) | `0x1C7C`: `charge` balances only while charging, `static` only at rest. The two modes are mutually exclusive |

Changing a number writes only that setting, then reads it back in a fresh session. If the BMS didn't keep the value, an error is shown. During every read or write, the pack's BMS_BLE entry is paused, because the BMS accepts only one BLE connection.

The `jbd_config.read_config` and `jbd_config.set_config` actions do the same for scripts. `read_config` also accepts raw `registers` for diagnostics.

`jbd_config.esup_read` (read-only, returns a response) queries the UP16S "ES-UP" second protocol (function `0x78`). On firmware 12.x this protocol holds the real 100% SOC reset point, because the firmware ignores DD writes to register `0x12`. `jbd_config.esup_set` writes `full_adjust_v`/`full_adjust_c`/`balance_mode` (function `0x79`, values that differ only), then verifies them in a fresh session. The ES-UP address is the parallel DIP address (here M1=1, S1=2, S2=3). The last address found is tried first.

Install: copy `custom_components/jbd_config` to the HA config folder and restart. Then add each pack under **Settings > Devices & services > Add integration > JBD BMS Config**. Packs listed in the `jbd_config:` YAML block are imported automatically. The Gentai firmware ignores register `0x2D` (balance mode). The CST (Pace BMS) is not supported because its BLE settings commands are undocumented.

Offline protocol tests: `python -m pytest tests -q`.

### [`tools/jbd_cell_full_voltage.py`](tools/jbd_cell_full_voltage.py)
Reads or sets the JBD BMS **Cell Full Voltage** (EEPROM register `0x12`, mV) over BLE. The BMS resets its SOC to 100% only when a cell reaches this voltage. Only that one register is written, unlike the vendor app's "BMS write", which rewrites every register. The default is read-only; `--mv` without `--write` is a dry run. After writing, the script reads the value back in a new session to confirm it was saved.

```bash
pip install bleak
python tools/jbd_cell_full_voltage.py                     # read M1/S1/S2
python tools/jbd_cell_full_voltage.py --mv 3540 --write   # set all three packs
```
Disable the pack's BMS_BLE entry in Home Assistant first, because the BMS accepts only one BLE connection at a time.

---

## Troubleshooting

### WinSCP Permission Denied when uploading to HA config folder

If you're running HA in Docker and logging in via WinSCP as a non-root user, all files in the config folder are owned by `root` by default, giving your user read-only access.

**Fix — transfer ownership of the files you need to edit (safe for Docker):**

```bash
sudo chown <your_user> ~/path/to/homeassistant_config/configuration.yaml
sudo chown <your_user> ~/path/to/homeassistant_config/automations.yaml
```

**Verify:**
```bash
ls -lah ~/path/to/homeassistant_config/configuration.yaml
```
You should see your username instead of `root` in the owner column.

> HA running inside Docker accesses files as root inside the container, so changing host ownership to your user has no effect on HA's ability to read/write the files.

---

## Notes

- All A/C sequences use a soft-off approach (ramp to 30°C → fan only → off) to reduce compressor stress.
- The `A/C 3F Power Meter - Auto Turn On` automation is controlled by `input_boolean.auto_turn_on_managed`. When the toggle is **off**, Auto Turn On stays permanently disabled. When **on**, it is re-enabled at sunrise and when battery reaches 100%.
- Device IDs in the YAML are specific to this installation and will need to be updated if migrating to a different HA instance.

---

## Support

If this project has been useful to you, consider buying me a coffee ☕

### <img src="docs/logo-ln.png" width="20"> Lightning
<img src="docs/qr-lightning.png" width="200"><br>
`greatjogging67@walletofsatoshi.com`

### <img src="docs/logo-xrp.png" width="20"> XRP
<img src="docs/qr-xrp.png" width="200"><br>
`rpWJmMcPM4ynNfvhaZFYmPhBq5FYfDJBZu`<br>
Destination Tag: `2135058530`

### <img src="docs/logo-btc.png" width="20"> BTC
<img src="docs/qr-btc.png" width="200"><br>
`bc1q5tqqew0wlpkdz22crltreu5ngc9sdje9hzr4vv`
