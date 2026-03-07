# "Visa" Introduction

Visa is old norse for "Show" and thats what this project is about.

## First boot:
 - Activate a AP "LED_MATRIX"
 - Configure WiFi and an MQTT broker

## Second boot and thereafter:
 - Publish to MQTT broker what IP it has got
 - Other configuration is acomplished by publish to specific MQTT topics.

# MQTT Topic Reference

All topics use the pattern `<hostname>/<suffix>`.

Default hostname is `LED_MATRIX`. Replace with your configured hostname throughout.

All values are persisted to EEPROM unless noted otherwise.

---

## Runtime topics

### `LED_MATRIX/value`
Display a message.
- Long messages (wider than display) scroll continuously.
- Short messages (fits on display) are shown statically.
- A queued message waits until the current scroll pass completes before displaying.
- Supports Swedish characters: `å ä ö Å Ä Ö`
- Supports degree symbol: `°` (UTF-8 `0xC2 0xB0`, auto-transcoded to Latin-1 `0xB0`)

```bash
# Plain text
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/value" -m "Hello World"

# Swedish characters
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/value" -m "Hej Sverige åäö"

# Temperature with degree symbol
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/value" -m "Temp: 22°C"
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/value" -m "Ute: -5°C  Inne: 21°C"

# Time and date (shell substitution)
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/value" -m "$(date +'%H:%M')"
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/value" -m "$(date +'%A %d %B')"

# Combined sensor readout
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/value" -m "$(date +'%H:%M')  Temp: 22°C  Luftfukt: 45%"

# Clear the display
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/value" -m ""
```

---

### `LED_MATRIX/speed`
Scroll step delay in milliseconds. Lower = faster scroll.

Range: `10` – `500` ms. Default: `100`

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/speed" -m "50"    # fast
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/speed" -m "100"   # default
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/speed" -m "200"   # slow
```

---

### `LED_MATRIX/intensity`
Display brightness.
Range: `0` (minimum) – `15` (maximum). Default: `5`

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/intensity" -m "0"    # minimum (nearly off)
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/intensity" -m "5"    # default
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/intensity" -m "15"   # maximum
```

---

### `LED_MATRIX/state`
Turn display on or off.

`1` = on, `0` = off. Not persisted — resets to on after reboot.

When turned back on the previous message resumes automatically.

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/state" -m "0"   # off
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/state" -m "1"   # on
```

---

### `LED_MATRIX/anim`
Animation mode for scrolling messages.

Default: `0`

| Value | Name         | Description                                    |
|-------|------        |-------------                                   |
| `0`   | Scroll left  | Text enters from right, exits left *(default)* |
| `1`   | Scroll right | Text enters from left, exits right             |
| `2`   | Scroll up    | Text shifts upward each tick                   |
| `3`   | Scroll down  | Text shifts downward each tick                 |
| `4`   | Flip LR      | Static left↔right mirror, redrawn each tick    |
| `5`   | Flip UD      | Static top↔bottom mirror, redrawn each tick    |
| `6`   | Rotate CW    | 90° clockwise rotation applied each tick       |
| `7`   | Invert       | All pixels toggled each tick                   |
| `8`   | Bounce       | Ping-pong: scroll left then right alternating  |
| `9`   | Scroll left + invert | Scroll left with inverted pixels       |
| `10`  | Blink        | Whole display blinks at scroll speed           |
| `11`  | Wipe in      | Columns fill inward from both edges            |
| `12`  | Wipe out     | Columns clear outward from centre              |

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/anim" -m "0"    # scroll left (default)
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/anim" -m "1"    # scroll right
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/anim" -m "8"    # bounce
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/anim" -m "10"   # blink
```

---

### `LED_MATRIX/pause`
Hold time in milliseconds after each full message pass before restarting.

`0` = no pause (continuous). Max: `30000` ms. Default: `0`

Behaviour differs by message length:
- **Long message (scrolling):** display is blank during the pause.
- **Short message (static):** message stays visible during the pause, then clears briefly before redisplaying.

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/pause" -m "0"      # continuous (default)
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/pause" -m "2000"   # 2 second pause
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/pause" -m "5000"   # 5 second pause
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/pause" -m "10000"  # 10 second pause
```

---

### `LED_MATRIX/align`
Text alignment for short messages that fit within the display width.

Has no effect on scrolling messages. Default: `0`

| Value | Alignment |
|-------|-----------|
| `0` | Left *(default)* |
| `1` | Centre |
| `2` | Right |

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/align" -m "0"   # left (default)
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/align" -m "1"   # centre — good for time "12:45"
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/align" -m "2"   # right
```

---

## Hardware config topics

Changes to hardware config topics trigger a full matrix reinitialisation without reboot.

The display briefly shows `reconfigured!` then resumes the previous message.

### `LED_MATRIX/config/hwtype`
MAX7219 module hardware type.

Default: `3` (FC16)

| Value | Type |
|-------|------|
| `0` | PAROLA_HW |
| `1` | GENERIC_HW |
| `2` | ICSTATION_HW |
| `3` | FC16_HW *(default)* |

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/config/hwtype" -m "3"   # FC16 (most common)
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/config/hwtype" -m "1"   # Generic
```

---

### `LED_MATRIX/config/numdevices`
Number of chained MAX7219 modules.

Range: `1` – `8`. Default: `4`

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/config/numdevices" -m "4"   # 4 modules
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/config/numdevices" -m "8"   # 8 modules
```

---

### `LED_MATRIX/config/clkpin`
GPIO number for SPI CLK.

Default (ESP8266 D1 Mini): `14` (D5)

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/config/clkpin" -m "14"
```

---

### `LED_MATRIX/config/datapin`
GPIO number for SPI DATA/MOSI.

Default (ESP8266 D1 Mini): `13` (D7)

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/config/datapin" -m "13"
```

---

### `LED_MATRIX/config/cspin`
GPIO number for SPI CS/SS.

Default (ESP8266 D1 Mini): `12` (D6)

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/config/cspin" -m "12"
```

---

### `LED_MATRIX/config/auxpin`
GPIO number for auxiliary output (e.g. LDR enable).

Default (ESP8266): `3`

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/config/auxpin" -m "3"
```

---

### `LED_MATRIX/config/hbledpin`
GPIO number for heartbeat LED.

Default (ESP8266): `2`

```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/config/hbledpin" -m "2"
```

---

### `matrix/config/hostname`
Rename the device. **Fixed topic** — always `matrix/config/hostname` regardless of current hostname, so any device can be targeted before its hostname is known.

Max 31 characters. Takes effect immediately — device reconnects to MQTT with the new name.

```bash
mosquitto_pub -h <MQTT broker> -t "matrix/config/hostname" -m "LOBBY"
mosquitto_pub -h <MQTT broker> -t "matrix/config/hostname" -m "RECEPTION"
mosquitto_pub -h <MQTT broker> -t "matrix/config/hostname" -m "LED_MATRIX"   # reset to default
```

---

## Status topic (published by device)

### `LED_MATRIX/config/status`
Published by the device on every MQTT connect and after errors.
Includes firmware version as the first field.

```
fw=2025-03-07.1 hostname=LED_MATRIX mqtt=<MQTT broker>:1883 hwType=3 devices=8 clk=14 data=13 cs=12 aux=3 hbled=2 speed=100 intensity=5 anim=0 pause=0 align=0
```

```bash
# Subscribe to watch status
mosquitto_sub -h <MQTT broker> -t "LED_MATRIX/config/status"

# Trigger a status republish by cycling state
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/state" -m "1"
```

---

## Active/online topic (published by device)

### `LED_MATRIX/active`
Published as `online` on every MQTT connect. Used by Home Assistant as the availability topic — entities show as unavailable if the device drops off the network.

```bash
mosquitto_sub -h <MQTT broker> -t "LED_MATRIX/active"
```

---

## Practical examples

### Clock display — centred, refreshed every minute
```bash
# Set up display style once
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/align"   -m "1"     # centre
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/pause"   -m "0"     # no pause
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/anim"    -m "0"

# Then update time each minute via cron or Home Assistant automation
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/value"   -m "$(date +'%H:%M')"
```

### Temperature ticker from Home Assistant
```bash
# Long scrolling message with temperature and humidity
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/value" -m "Ute: -3°C  Inne: 21°C  Luftfukt: 42%"
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/speed" -m "80"
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/pause" -m "3000"
```

### Night mode — dim and slow
```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/intensity" -m "1"
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/speed"     -m "150"
```

### Day mode — bright and fast
```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/intensity" -m "10"
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/speed"     -m "60"
```

### Turn off at bedtime, on at wake-up
```bash
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/state" -m "0"   # off — previous message saved
mosquitto_pub -h <MQTT broker> -t "LED_MATRIX/state" -m "1"   # on  — previous message resumes
```
