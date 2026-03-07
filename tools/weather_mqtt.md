# weather_mqtt – Weather → MQTT Publisher

Fetch current weather (and optionally a short-term forecast) from
**Open-Meteo** or **wttr.in** and publish the selected fields to an MQTT
broker. Everything is driven by a single `config.yaml` file.

---

## Files

| File | Purpose |
|------|---------|
| `weather_mqtt.py` | Main script |
| `config.yaml` | All configuration (edit this) |

---

## Requirements

Python 3.8 + and three pip packages:

```bash
pip install paho-mqtt requests pyyaml
```

---

## Quick start

1. **Edit `config.yaml`** – set your broker address, location, and the fields
   you want.
2. **Test without MQTT:**
   ```bash
   python weather_mqtt.py --dry-run --once
   ```
3. **Run once for real:**
   ```bash
   python weather_mqtt.py --once
   ```
4. **Run continuously** (publishes every `interval` seconds):
   ```bash
   python weather_mqtt.py
   ```

---

## Key configuration options

### MQTT

```yaml
mqtt:
  broker: "192.168.1.10"
  port: 1883
  username: "user"
  password: "secret"
  base_topic: "home/weather"
  topic_per_field: true   # each field → own sub-topic
  retain: true
  qos: 1
```

`topic_per_field: true` → publishes e.g.
- `home/weather/temperature` → `21.4`
- `home/weather/humidity` → `62`
- `home/weather/weather_description` → `Partly cloudy`

`topic_per_field: false` → publishes one JSON payload to `json_topic`.

### Weather service

```yaml
weather:
  service: "open-meteo"   # or "wttr.in"
  latitude: 59.3293
  longitude: 18.0686
  location_name: "Stockholm"   # used by wttr.in
  interval: 300                # seconds between fetches
```

### Units

```yaml
  units:
    temperature: "celsius"    # celsius | fahrenheit
    wind_speed: "kmh"         # kmh | mph | ms | knots
    precipitation: "mm"       # mm | inch
    timezone: "auto"          # open-meteo only
```

### Fields

```yaml
  fields:
    temperature: true
    feels_like: true
    humidity: true
    wind_speed: true
    wind_direction: true
    wind_gusts: true
    cloud_cover: true
    surface_pressure: true
    precipitation: true
    weather_code: true
    weather_description: true   # human-readable WMO description
    visibility: false
    is_day: false
    forecast_enabled: false     # open-meteo only
    forecast_hours: 24
```

---

## Running as a service (systemd)

```ini
# /etc/systemd/system/weather_mqtt.service
[Unit]
Description=Weather MQTT Publisher
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 /opt/weather_mqtt/weather_mqtt.py
WorkingDirectory=/opt/weather_mqtt
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now weather_mqtt
```
