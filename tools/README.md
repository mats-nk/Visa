# Weather MQTT – Documentation

A pair of Python scripts that fetch weather data and forward MQTT topics, driven entirely by YAML configuration files.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [weather_mqtt.py](#weather_mqttpy)
   - [Requirements](#requirements)
   - [Installation](#installation)
   - [Command-line Usage](#command-line-usage)
   - [Configuration Reference – config.yaml](#configuration-reference--configyaml)
   - [MQTT Topics Published](#mqtt-topics-published)
   - [Weather Fields](#weather-fields)
   - [Units](#units)
   - [Wind Direction Formats](#wind-direction-formats)
   - [Language Support](#language-support)
   - [Running as a Service](#running-as-a-service-weather_mqtt)
3. [mqtt_bridge.py](#mqtt_bridgepy)
   - [Requirements](#requirements-1)
   - [Command-line Usage](#command-line-usage-1)
   - [Configuration Reference – mqtt_bridge_config.yaml](#configuration-reference--mqtt_bridge_configyaml)
   - [Mapping Types](#mapping-types)
   - [Transform Pipeline](#transform-pipeline)
   - [Round-Robin Rotation](#round-robin-rotation)
   - [Wildcard Topics](#wildcard-topics)
   - [Running as a Service](#running-as-a-service-mqtt_bridge)
4. [YAML Tips](#yaml-tips)
5. [Version Numbering](#version-numbering)

---

## Quick Start

```bash
# 1. Create virtual environment and install dependencies
chmod +x setup.sh
./setup.sh

# 2. Edit config.yaml with your location and broker address
# 3. Run
source .venv/bin/activate
python weather_mqtt.py
```

---

## weather_mqtt.py

Fetches current weather conditions (and optionally a short-term forecast) from either **Open-Meteo** or **wttr.in**, and publishes the selected fields to an MQTT broker at a configurable interval.

### Requirements

Python 3.8+ and three packages:

```bash
pip install paho-mqtt requests pyyaml
```

### Installation

```bash
./setup.sh          # creates .venv, installs packages, runs a smoke test
```

Or manually:

```bash
python -m venv .venv
source .venv/bin/activate
pip install paho-mqtt requests pyyaml
```

### Command-line Usage

```
python weather_mqtt.py [options]

Options:
  --config FILE   Path to YAML config file  (default: config.yaml)
  --once          Fetch once and exit – ignores the interval setting
  --dry-run       Print payloads to stdout without connecting to MQTT
```

Examples:

```bash
# Test without touching the broker
python weather_mqtt.py --dry-run --once

# Use a custom config file
python weather_mqtt.py --config my_weather.yaml

# Run continuously (default)
python weather_mqtt.py
```

---

### Configuration Reference – config.yaml

#### `mqtt` section

| Key | Default | Description |
|-----|---------|-------------|
| `broker` | `localhost` | Hostname or IP of your MQTT broker |
| `port` | `1883` | Port – use `8883` for TLS |
| `username` | `""` | Leave empty if no authentication required |
| `password` | `""` | Leave empty if no authentication required |
| `client_id` | `weather_publisher` | MQTT client identifier |
| `tls` | `false` | Enable TLS/SSL |
| `ca_cert` | *(unset)* | Path to CA certificate file (TLS only) |
| `base_topic` | `weather` | Root topic prefix |
| `topic_per_field` | `true` | `true` = one sub-topic per field; `false` = single JSON payload |
| `json_topic` | `weather/all` | Topic used when `topic_per_field` is `false` |
| `retain` | `true` | Set the MQTT retain flag on published messages |
| `qos` | `1` | MQTT QoS level – `0`, `1`, or `2` |

#### `weather` section

| Key | Default | Description |
|-----|---------|-------------|
| `service` | `open-meteo` | Weather data source: `open-meteo` or `wttr.in` |
| `latitude` | — | Decimal degrees latitude |
| `longitude` | — | Decimal degrees longitude |
| `location_name` | — | Human-readable name – used by wttr.in as the lookup string |
| `interval` | `900` | Seconds between fetches (900 = every 15 minutes) |
| `language` | `en` | Language for weather descriptions – see [Language Support](#language-support) |

#### `weather.units` section

| Key | Default | Options | Description |
|-----|---------|---------|-------------|
| `temperature` | `celsius` | `celsius` / `fahrenheit` | Temperature unit |
| `wind_speed` | `ms` | `ms` / `kmh` / `mph` / `knots` | Wind speed unit |
| `wind_direction` | `16-compass` | `degrees` / `16-compass` / `12-compass` | Wind direction format |
| `precipitation` | `mm` | `mm` / `inch` | Precipitation unit |
| `timezone` | `auto` | Any IANA tz string | Open-Meteo only – `auto` uses the location's timezone |

#### `weather.fields` section

Each field can be set to `true` (publish) or `false` (skip).

| Field | Description | Service |
|-------|-------------|---------|
| `temperature` | Current temperature | Both |
| `feels_like` | Apparent / feels-like temperature | Both |
| `humidity` | Relative humidity (%) | Both |
| `precipitation` | Precipitation last hour | Both |
| `rain` | Rain component | open-meteo |
| `wind_speed` | Wind speed | Both |
| `wind_direction` | Wind direction (format per `units.wind_direction`) | Both |
| `wind_gusts` | Wind gust speed | Both |
| `cloud_cover` | Cloud cover (%) | Both |
| `visibility` | Visibility (metres) | open-meteo |
| `surface_pressure` | Atmospheric pressure (hPa) | Both |
| `weather_code` | WMO numeric weather code | Both |
| `weather_description` | Human-readable description of the weather code | Both |
| `is_day` | `1` = daytime, `0` = night | open-meteo |
| `forecast_enabled` | Enable hourly forecast | open-meteo |
| `forecast_hours` | How many hours of forecast to include | open-meteo |
| `forecast_temperature_max` | Max temperature per forecast hour | open-meteo |
| `forecast_temperature_min` | Min temperature per forecast hour | open-meteo |
| `forecast_precipitation_sum` | Precipitation sum per forecast hour | open-meteo |
| `forecast_wind_speed_max` | Max wind speed per forecast hour | open-meteo |
| `forecast_weather_code` | Weather code per forecast hour | open-meteo |

#### `logging` section

| Key | Default | Description |
|-----|---------|-------------|
| `level` | `INFO` | Log level: `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `log_to_file` | `false` | Also write log output to a file |
| `log_file` | `weather_mqtt.log` | Log file path (only used when `log_to_file: true`) |

---

### MQTT Topics Published

With `base_topic: weather` and `topic_per_field: true`:

```
weather/version               → 2026-03-07.1
weather/temperature           → 21.4
weather/feels_like            → 19.8
weather/humidity              → 62
weather/wind_speed            → 4.2
weather/wind_direction        → WSW
weather/wind_gusts            → 7.1
weather/precipitation         → 0.0
weather/cloud_cover           → 45
weather/surface_pressure      → 1013.2
weather/weather_code          → 2
weather/weather_description   → Halvklart
weather/timestamp             → 2026-03-07T20:33:00+00:00
weather/source                → open-meteo
```

With `topic_per_field: false`, all fields are published as a single JSON object to `weather/all`.

---

### Weather Fields

All numeric values are plain strings (no unit suffix) so downstream consumers can parse them as numbers directly. Use `mqtt_bridge.py` with a `suffix` transform if you need a unit appended.

---

### Units

**Temperature:** `celsius` or `fahrenheit`

**Wind speed:**

| Value | Unit |
|-------|------|
| `ms` | metres per second (default) |
| `kmh` | kilometres per hour |
| `mph` | miles per hour |
| `knots` | knots |

**Precipitation:** `mm` or `inch`

---

### Wind Direction Formats

| Value | Example output | Sector size |
|-------|---------------|-------------|
| `degrees` | `247` | raw 0–360° float |
| `16-compass` | `WSW` | 22.5° per sector |
| `12-compass` | `W` | 30° per sector |

---

### Language Support

Set `language` under the `weather` key. Descriptions fall back to English for any untranslated code.

| Code | Language |
|------|----------|
| `en` | English |
| `sv` | Swedish |
| `de` | German |
| `fr` | French |
| `nl` | Dutch |
| `es` | Spanish |
| `no` | Norwegian |
| `fi` | Finnish |
| `da` | Danish |
| `pl` | Polish |

To add a language, add a new key to `_WMO_TRANSLATIONS` in `weather_mqtt.py`. The key is the ISO 639-1 code in lower-case.

---

### Running as a Service (weather_mqtt)

```ini
# /etc/systemd/system/weather_mqtt.service
[Unit]
Description=Weather MQTT Publisher
After=network-online.target

[Service]
ExecStart=/opt/weather/. .venv/bin/activate && python weather_mqtt.py
WorkingDirectory=/opt/weather
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now weather_mqtt
```

---

## mqtt_bridge.py

Subscribes to MQTT topics on a source broker and republishes them to a destination broker (which can be the same broker). Supports value transforms, wildcard subscriptions, and automatic round-robin rotation when multiple sources share the same destination topic.

### Requirements

Python 3.8+ and two packages:

```bash
pip install paho-mqtt pyyaml
```

### Command-line Usage

```
python mqtt_bridge.py [options]

Options:
  --config FILE   Path to YAML config file  (default: mqtt_bridge_config.yaml)
  --dry-run       Log all forwards without publishing to destination
  --list          Print active mappings and exit
```

Examples:

```bash
# See all active mappings
python mqtt_bridge.py --list

# Test without publishing anything
python mqtt_bridge.py --dry-run

# Use a custom config
python mqtt_bridge.py --config home_bridge.yaml
```

Sample `--list` output:

```
mqtt_bridge.py  v2026-03-07.1
────────────────────────────────────────────────────────────
  Active mappings  (2):
    weather/temperature             →  house/outside/temperature
    weather/humidity                →  house/outside/humidity

  Active round-robin group  (1):
    → LED_MATRIX/value
      [1] weather/temperature    [round:0 → suffix: °C → prefix:Ute:  ]
      [2] weather/wind_speed     [round:1 → suffix: m/s → prefix:Blåst:  ]
      [3] weather/humidity       [round:2 → suffix: % → prefix:Fukt:  ]

  Disabled mappings  (3):
    ...
```

---

### Configuration Reference – mqtt_bridge_config.yaml

#### `source` section

| Key | Default | Description |
|-----|---------|-------------|
| `broker` | `localhost` | Source broker hostname or IP |
| `port` | `1883` | Source broker port |
| `username` | `""` | Authentication username |
| `password` | `""` | Authentication password |
| `client_id` | `mqtt_bridge_source` | MQTT client identifier |
| `tls` | `false` | Enable TLS |
| `ca_cert` | *(unset)* | CA certificate path (TLS only) |

#### `destination` section

Same keys as `source`, plus:

| Key | Default | Description |
|-----|---------|-------------|
| `client_id` | `mqtt_bridge_destination` | MQTT client identifier |
| `retain` | `true` | Global retain flag for published messages |
| `qos` | `1` | Global QoS for published messages |

> Source and destination can point to the same broker — topic names must then differ to avoid loops.

#### `bridge` section

| Key | Default | Description |
|-----|---------|-------------|
| `heartbeat_interval` | `60` | Seconds between heartbeats and round-robin advances. Set to `0` to disable. |
| `version_topic` | `bridge/version` | Topic where the script version is published on startup and each heartbeat |

#### `logging` section

| Key | Default | Description |
|-----|---------|-------------|
| `level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `log_to_file` | `false` | Write log output to a file |
| `log_file` | `mqtt_bridge.log` | Log file path |

---

### Mapping Types

#### Normal mapping

Forwards every received message immediately.

```yaml
mappings:
  - from: "weather/temperature"
    to:   "house/outside/temperature"
    enabled: true
```

| Key | Default | Description |
|-----|---------|-------------|
| `from` | — | Source topic to subscribe to |
| `to` | — | Destination topic to publish to |
| `enabled` | `true` | Set `false` to disable without deleting |
| `retain` | *(global)* | Override global retain for this mapping |
| `qos` | *(global)* | Override global QoS for this mapping |
| `transform` | *(none)* | Single transform string or list of steps |
| `round_robin` | `false` | Force round-robin even with a unique destination |

#### Round-robin mapping

When two or more enabled mappings share the same `to` topic, they are **automatically grouped** into a round-robin. On each heartbeat, only one source is published to the shared destination, rotating through the list in order.

```yaml
mappings:
  - from: "weather/temperature"
    to:   "LED_MATRIX/value"
    transform:
      - "round:0"
      - "suffix: °C"
      - 'prefix:Ute:  '

  - from: "weather/wind_speed"
    to:   "LED_MATRIX/value"
    transform:
      - "round:1"
      - "suffix: m/s"
      - 'prefix:Blåst:  '

  - from: "weather/humidity"
    to:   "LED_MATRIX/value"
    transform:
      - "round:0"
      - "suffix: %"
      - 'prefix:Fukt:  '
```

Rotation sequence per heartbeat:

```
Heartbeat 1 → weather/temperature → LED_MATRIX/value : Ute:  21 °C
Heartbeat 2 → weather/wind_speed  → LED_MATRIX/value : Blåst:  3.2 m/s
Heartbeat 3 → weather/humidity    → LED_MATRIX/value : Fukt:  62 %
Heartbeat 4 → weather/temperature → LED_MATRIX/value : Ute:  21 °C  (wraps)
```

> The `heartbeat_interval` in the `bridge` section controls how quickly the rotation advances.

---

### Transform Pipeline

A `transform` can be a **single string** or a **YAML list** applied left-to-right:

```yaml
# Single step
transform: "round:1"

# Pipeline – output of each step feeds into the next
transform:
  - "round:1"
  - "suffix: °C"
  - 'prefix:Temp:  '
```

#### Available transforms

| Transform | Example | Input | Output |
|-----------|---------|-------|--------|
| `passthrough` | `passthrough` | `21.357` | `21.357` |
| `round:N` | `round:1` | `21.357` | `21.4` |
| `scale:F` | `scale:0.1` | `213.57` | `21.357` |
| `offset:N` | `offset:-273.15` | `294.15` | `21.0` |
| `upper` | `upper` | `partly cloudy` | `PARTLY CLOUDY` |
| `lower` | `lower` | `PARTLY CLOUDY` | `partly cloudy` |
| `suffix:STR` | `suffix: °C` | `21.4` | `21.4 °C` |
| `prefix:STR` | `'prefix:Ute:  '` | `21.4` | `Ute:  21.4` |
| `json_field:KEY` | `json_field:temperature` | `{"temperature":21.4}` | `21.4` |
| `template:STR` | `template:Temp is {value}` | `21.4` | `Temp is 21.4` |

> **YAML tip:** When your `prefix` or `suffix` ends with spaces, always use **single quotes** to prevent YAML from stripping trailing whitespace:
> ```yaml
> transform: 'prefix:Ute:  '   # ✓ spaces preserved
> transform: "prefix:Ute:  "   # ✗ trailing spaces may be stripped
> ```

---

### Wildcard Topics

MQTT wildcards `+` (single level) and `#` (multi-level) are supported in `from`.
The matched topic segments can be used in the `to` pattern:

| Placeholder | Meaning |
|-------------|---------|
| `{topic}` | Full source topic string |
| `{0}`, `{1}`, … | Individual topic levels (0-indexed) |

```yaml
# Forward every sensor's temperature to an archive topic
- from: "sensors/+/temperature"
  to:   "archive/{1}/temp"
  # sensors/kitchen/temperature → archive/kitchen/temp
  # sensors/garden/temperature  → archive/garden/temp

# Archive everything under weather/
- from: "weather/#"
  to:   "archive/{1}"
  # weather/temperature → archive/temperature
  # weather/humidity    → archive/humidity
```

---

### Running as a Service (mqtt_bridge)

```ini
# /etc/systemd/system/mqtt_bridge.service
[Unit]
Description=MQTT Topic Bridge
After=network-online.target

[Service]
ExecStart=/opt/weather/.venv/bin/python /opt/weather/mqtt_bridge.py
WorkingDirectory=/opt/weather
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now mqtt_bridge
```

---

## YAML Tips

**Indentation** — YAML uses spaces, never tabs. All items in the same list must align exactly:

```yaml
# ✓ correct – all dashes at the same column
transform:
  - "round:1"
  - "suffix: °C"

# ✗ wrong – mixed indentation causes a parser error
transform:
      - "round:1"
     - "suffix: °C"
```

**One key per entry** — duplicate keys are silently overwritten:

```yaml
# ✗ wrong – second transform silently replaces the first
- from: "weather/temperature"
  transform: "round:1"
  transform: "suffix: °C"

# ✓ correct – use a list
- from: "weather/temperature"
  transform:
    - "round:1"
    - "suffix: °C"
```

**Trailing spaces** — always use single quotes when your value ends with spaces:

```yaml
transform: 'prefix:Ute:  '    # ✓ spaces kept
transform: "prefix:Ute:  "    # ✗ spaces may be stripped
```

**`mappings:` must be a top-level key** — do not nest it inside `bridge:` or any other section:

```yaml
bridge:
  heartbeat_interval: 10

mappings:           # ✓ top-level
  - from: "..."
```

---

## Version Numbering

Both scripts use the format `YYYY-MM-DD.N` where `N` is a sequence number starting at 1 for that date.

```python
__VERSION__ = "2026-03-07.1"
```

The version is published as an MQTT topic on startup (and on every heartbeat for `mqtt_bridge.py`):

| Script | Topic |
|--------|-------|
| `weather_mqtt.py` | `{base_topic}/version` e.g. `weather/version` |
| `mqtt_bridge.py` | configured by `bridge.version_topic` e.g. `bridge/version` |

To bump the version after making changes, edit the `__VERSION__` line near the top of the relevant script.
