# Home Assistant Automations

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
| `sensor.srne_battery` | Solar battery SOC (%) |
| `climate.ac` | 3rd-floor A/C unit (IR/WiFi climate entity) |
| A/C 3F Power Meter (`device_id: 73be0b81…`) | Smart plug monitoring the A/C circuit |
| `sensor.a_c_power_meter_power_filtered_5m` | Filtered real-time power draw of the A/C — 5 min average (W) |
| `sensor.a_c_power_meter_power_filtered_10m` | Filtered real-time power draw of the A/C — 10 min average (W) |
| `sensor.a_c_power_meter_power_filtered_15m` | Filtered real-time power draw of the A/C — 15 min average (W) |
| `sensor.a_c_power_meter_power_filtered_20m` | Filtered real-time power draw of the A/C — 20 min average (W) |
| `sensor.a_c_power_meter_power_filtered_25m` | Filtered real-time power draw of the A/C — 25 min average (W) |
| `sensor.a_c_power_meter_power_filtered_30m` | Filtered real-time power draw of the A/C — 30 min average (W) |
| `input_boolean.battery_reached_100_today` | Flag: battery reached full charge today |
| `input_datetime.a_c_last_adjusted` | Timestamp of last A/C temperature adjustment (cooldown tracking) |
| EF-R30241 (`device_id: bbdfa75a…`) | Portable power station |
| Roof Deck Light (`device_id: 8510f0c0…`) | Outdoor roof deck switch |

---

## Automations

### ☀️ Solar Battery / SOC Management

#### [`Daily SOC Check`](automations/daily_soc_check.yaml)
Tracks whether the battery reached full charge (>99%) during the day.

- **On battery full** (daytime only): sets `input_boolean.battery_reached_100_today` to `on` and re-enables `automation.roof_deck_light` (if disabled)
- **On sunrise**: resets the flag to `off` and re-enables the *Auto Turn On* automation (if disabled)
- **On sunset** (if battery never reached 100% today): turns off the A/C power meter switch and disables the *Auto Turn On* automation

> All enable/disable actions use idempotent guards — they only act if the automation is not already in the desired state.

---

### ❄️ A/C 3F Power Meter — Circuit Switch Control

These automations manage the smart plug that supplies the A/C.

#### [`A/C 3F Power Meter - Auto Turn On SOC`](automations/ac_3f_power_meter_auto_turn_on_soc.yaml)
When `input_boolean.battery_reached_100_today` turns `on`:
- Re-enables the *Auto Turn On* automation (if it was disabled)
- Immediately turns on the A/C power meter switch

#### [`A/C 3F Power Meter - Auto Turn On`](automations/ac_3f_power_meter_auto_turn_on.yaml)
Turns the A/C power meter switch **on** at progressive time windows, gated by battery SOC:

| Time | Min Battery SOC |
|------|----------------|
| 10:00 | > 45% |
| 11:00 | > 60% |
| 12:00 | > 75% |
| 13:00 | > 90% |

Only fires if the switch is currently off.

#### [`A/C 3F Power Meter - Auto Turn Off`](automations/ac_3f_power_meter_auto_turn_off.yaml)
Turns the A/C power meter switch **off** at progressive time windows if the battery has dropped below a threshold:

| Time | Max Battery SOC |
|------|----------------|
| 09:00 | < 30% |
| 11:00 | < 60% |
| 12:00 | < 70% |
| 13:00 | < 80% |
| 14:00 | < 90% |

Only fires if the switch is currently on.

#### [`A/C 3F Power Meter - 3.0 kWh Limit`](automations/ac_3f_power_meter_30_kwh_limit.yaml)
Turns the A/C power meter switch **off** if total energy consumption exceeds **3.0 kWh** (and has been above it for 1 minute). Active between 15:00 and 09:00 only.

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

#### [`A/C Night OFF Sequence - Sunrise`](automations/ac_night_off_sequence_sunrise.yaml)
At sunrise, if the A/C is still running:

Sequence: set temp to 30°C → 10 s delay → `fan_only` → 15 min delay → `off`

---

### 🌅 A/C Afternoon Sequence

#### [`A/C Afternoon OFF Sequence`](automations/ac_afternoon_off_sequence.yaml)
Between **15:30–18:00**, if the solar battery drops below 97% SOC for 1 minute and the A/C is not off:

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
- The `A/C 3F Power Meter - Auto Turn On` automation is dynamically enabled/disabled by the `Daily SOC Check` and `A/C 3F Power Meter - Auto Turn On SOC` automations to prevent the A/C from turning on when the battery did not reach full charge that day.
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
