#!/usr/bin/env python3
"""
weather_mqtt.py
~~~~~~~~~~~~~~~
Fetch current weather (and optionally a short forecast) from either
Open-Meteo (https://open-meteo.com) or wttr.in (https://wttr.in) and
publish the selected fields to an MQTT broker.

All settings are read from config.yaml (or the path given with --config).

Usage:
    python weather_mqtt.py                     # uses config.yaml
    python weather_mqtt.py --config my.yaml    # custom config path
    python weather_mqtt.py --once              # fetch once, then exit
    python weather_mqtt.py --dry-run           # print payloads, skip MQTT

Dependencies (install with pip):
    pip install paho-mqtt requests pyyaml
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml

# ── Script version ───────────────────────────────────────────────────────────
__VERSION__ = "2026-03-07.1"   # format: YYYY-MM-DD.sequence

# ── paho-mqtt (v1 and v2 have different APIs) ────────────────────────────────
try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit(
        "paho-mqtt is not installed. Run:  pip install paho-mqtt requests pyyaml"
    )

# Detect paho-mqtt major version robustly – __version__ location differs by release
try:
    import paho
    _paho_ver = getattr(paho, "__version__", None)
    if _paho_ver is None:
        from importlib.metadata import version as _pkg_ver
        _paho_ver = _pkg_ver("paho-mqtt")
    _PAHO_V2 = int(_paho_ver.split(".")[0]) >= 2
except Exception:
    # Last resort: v2 introduced CallbackAPIVersion; if present we're on v2
    _PAHO_V2 = hasattr(mqtt, "CallbackAPIVersion")

# ── WMO weather-code descriptions (multi-language) ──────────────────────────
#
# Add or extend any language by adding a new key to _WMO_TRANSLATIONS.
# The key is the ISO-639-1 language code (lower-case).
# "en" is the built-in fallback and must always be complete.
#
_WMO_TRANSLATIONS: dict[str, dict[int, str]] = {
    "en": {
        0: "Clear sky",
        1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Fog", 48: "Depositing rime fog",
        51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
        56: "Light freezing drizzle", 57: "Heavy freezing drizzle",
        61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
        66: "Light freezing rain", 67: "Heavy freezing rain",
        71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
        77: "Snow grains",
        80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
        85: "Slight snow showers", 86: "Heavy snow showers",
        95: "Thunderstorm", 96: "Thunderstorm w/ slight hail", 99: "Thunderstorm w/ heavy hail",
    },
    "sv": {   # Swedish
        0: "Klar himmel",
        1: "Mestadels klart", 2: "Halvklart", 3: "Mulet",
        45: "Dimma", 48: "Underkyld dimma",
        51: "Lätt duggregn", 53: "Måttligt duggregn", 55: "Tätt duggregn",
        56: "Lätt underkylt duggregn", 57: "Kraftigt underkylt duggregn",
        61: "Lätt regn", 63: "Måttligt regn", 65: "Kraftigt regn",
        66: "Lätt underkylt regn", 67: "Kraftigt underkylt regn",
        71: "Lätt snöfall", 73: "Måttligt snöfall", 75: "Kraftigt snöfall",
        77: "Snögryn",
        80: "Lätta regnskurar", 81: "Måttliga regnskurar", 82: "Våldsamma regnskurar",
        85: "Lätta snöbyar", 86: "Kraftiga snöbyar",
        95: "Åskväder", 96: "Åskväder med lätt hagel", 99: "Åskväder med kraftigt hagel",
    },
    "de": {   # German
        0: "Klarer Himmel",
        1: "Überwiegend klar", 2: "Teilweise bewölkt", 3: "Bedeckt",
        45: "Nebel", 48: "Gefrierender Nebel",
        51: "Leichter Nieselregen", 53: "Mäßiger Nieselregen", 55: "Dichter Nieselregen",
        56: "Leichter gefrierender Nieselregen", 57: "Schwerer gefrierender Nieselregen",
        61: "Leichter Regen", 63: "Mäßiger Regen", 65: "Starker Regen",
        66: "Leichter gefrierender Regen", 67: "Starker gefrierender Regen",
        71: "Leichter Schneefall", 73: "Mäßiger Schneefall", 75: "Starker Schneefall",
        77: "Schneekörner",
        80: "Leichte Regenschauer", 81: "Mäßige Regenschauer", 82: "Heftige Regenschauer",
        85: "Leichte Schneeschauer", 86: "Starke Schneeschauer",
        95: "Gewitter", 96: "Gewitter mit leichtem Hagel", 99: "Gewitter mit starkem Hagel",
    },
    "fr": {   # French
        0: "Ciel dégagé",
        1: "Principalement dégagé", 2: "Partiellement nuageux", 3: "Couvert",
        45: "Brouillard", 48: "Brouillard givrant",
        51: "Bruine légère", 53: "Bruine modérée", 55: "Bruine dense",
        56: "Bruine verglaçante légère", 57: "Bruine verglaçante forte",
        61: "Pluie légère", 63: "Pluie modérée", 65: "Pluie forte",
        66: "Pluie verglaçante légère", 67: "Pluie verglaçante forte",
        71: "Neige légère", 73: "Neige modérée", 75: "Neige forte",
        77: "Grains de neige",
        80: "Averses légères", 81: "Averses modérées", 82: "Averses violentes",
        85: "Averses de neige légères", 86: "Averses de neige fortes",
        95: "Orage", 96: "Orage avec grêle légère", 99: "Orage avec grêle forte",
    },
    "nl": {   # Dutch
        0: "Heldere hemel",
        1: "Overwegend helder", 2: "Gedeeltelijk bewolkt", 3: "Bewolkt",
        45: "Mist", 48: "Aanvriezende mist",
        51: "Lichte motregen", 53: "Matige motregen", 55: "Dichte motregen",
        56: "Lichte onderkoelde motregen", 57: "Zware onderkoelde motregen",
        61: "Lichte regen", 63: "Matige regen", 65: "Zware regen",
        66: "Lichte onderkoelde regen", 67: "Zware onderkoelde regen",
        71: "Lichte sneeuwval", 73: "Matige sneeuwval", 75: "Zware sneeuwval",
        77: "Sneeuwkorrels",
        80: "Lichte regenbuien", 81: "Matige regenbuien", 82: "Hevige regenbuien",
        85: "Lichte sneeuwbuien", 86: "Zware sneeuwbuien",
        95: "Onweer", 96: "Onweer met lichte hagel", 99: "Onweer met zware hagel",
    },
    "es": {   # Spanish
        0: "Cielo despejado",
        1: "Mayormente despejado", 2: "Parcialmente nublado", 3: "Nublado",
        45: "Niebla", 48: "Niebla engelante",
        51: "Llovizna ligera", 53: "Llovizna moderada", 55: "Llovizna densa",
        56: "Llovizna engelante ligera", 57: "Llovizna engelante intensa",
        61: "Lluvia ligera", 63: "Lluvia moderada", 65: "Lluvia intensa",
        66: "Lluvia engelante ligera", 67: "Lluvia engelante intensa",
        71: "Nieve ligera", 73: "Nieve moderada", 75: "Nieve intensa",
        77: "Granos de nieve",
        80: "Chubascos ligeros", 81: "Chubascos moderados", 82: "Chubascos violentos",
        85: "Chubascos de nieve ligeros", 86: "Chubascos de nieve intensos",
        95: "Tormenta", 96: "Tormenta con granizo ligero", 99: "Tormenta con granizo intenso",
    },
    "no": {   # Norwegian
        0: "Klar himmel",
        1: "Stort sett klart", 2: "Delvis skyet", 3: "Overskyet",
        45: "Tåke", 48: "Underkjølt tåke",
        51: "Lett yr", 53: "Moderat yr", 55: "Tett yr",
        56: "Lett underkjølt yr", 57: "Kraftig underkjølt yr",
        61: "Lett regn", 63: "Moderat regn", 65: "Kraftig regn",
        66: "Lett underkjølt regn", 67: "Kraftig underkjølt regn",
        71: "Lett snøfall", 73: "Moderat snøfall", 75: "Kraftig snøfall",
        77: "Snøkorn",
        80: "Lette regnbyger", 81: "Moderate regnbyger", 82: "Voldsome regnbyger",
        85: "Lette snøbyger", 86: "Kraftige snøbyger",
        95: "Tordenvær", 96: "Tordenvær med lett hagl", 99: "Tordenvær med kraftig hagl",
    },
    "fi": {   # Finnish
        0: "Selkeää",
        1: "Pääosin selkeää", 2: "Puolipilvistä", 3: "Pilvistä",
        45: "Sumua", 48: "Jäätävää sumua",
        51: "Kevyttä tihkusadetta", 53: "Kohtalaista tihkusadetta", 55: "Tiheää tihkusadetta",
        56: "Kevyttä jäätävää tihkua", 57: "Raskasta jäätävää tihkua",
        61: "Kevyttä sadetta", 63: "Kohtalaista sadetta", 65: "Raskasta sadetta",
        66: "Kevyttä jäätävää sadetta", 67: "Raskasta jäätävää sadetta",
        71: "Kevyttä lumisadetta", 73: "Kohtalaista lumisadetta", 75: "Raskasta lumisadetta",
        77: "Lumirakeita",
        80: "Kevyitä sadekuuroja", 81: "Kohtalaisia sadekuuroja", 82: "Voimakkaita sadekuuroja",
        85: "Kevyitä lumikuuroja", 86: "Voimakkaita lumikuuroja",
        95: "Ukkonen", 96: "Ukkonen kevyen raesateen kanssa", 99: "Ukkonen voimakkaan raesateen kanssa",
    },
    "da": {   # Danish
        0: "Klar himmel",
        1: "Overvejende klart", 2: "Delvist skyet", 3: "Overskyet",
        45: "Tåge", 48: "Rimtåge",
        51: "Let støvregn", 53: "Moderat støvregn", 55: "Tæt støvregn",
        56: "Let frysende støvregn", 57: "Kraftig frysende støvregn",
        61: "Let regn", 63: "Moderat regn", 65: "Kraftig regn",
        66: "Let frysende regn", 67: "Kraftig frysende regn",
        71: "Let sne", 73: "Moderat sne", 75: "Kraftig sne",
        77: "Snekorn",
        80: "Lette regnbyger", 81: "Moderate regnbyger", 82: "Voldsomme regnbyger",
        85: "Lette sne byger", 86: "Kraftige sne byger",
        95: "Tordenvejr", 96: "Tordenvejr med let hagl", 99: "Tordenvejr med kraftigt hagl",
    },
    "pl": {   # Polish
        0: "Bezchmurnie",
        1: "Przeważnie bezchmurnie", 2: "Częściowe zachmurzenie", 3: "Pochmurno",
        45: "Mgła", 48: "Oszroniona mgła",
        51: "Lekka mżawka", 53: "Umiarkowana mżawka", 55: "Gęsta mżawka",
        56: "Lekka marznąca mżawka", 57: "Silna marznąca mżawka",
        61: "Lekki deszcz", 63: "Umiarkowany deszcz", 65: "Silny deszcz",
        66: "Lekki marznący deszcz", 67: "Silny marznący deszcz",
        71: "Lekki śnieg", 73: "Umiarkowany śnieg", 75: "Silny śnieg",
        77: "Ziarna śniegu",
        80: "Lekkie przelotne opady", 81: "Umiarkowane przelotne opady", 82: "Gwałtowne opady",
        85: "Lekkie opady śniegu", 86: "Silne opady śniegu",
        95: "Burza", 96: "Burza z lekkim gradem", 99: "Burza z silnym gradem",
    },
}

# Convenience alias kept for any code that references WMO_CODES directly
WMO_CODES = _WMO_TRANSLATIONS["en"]


def wmo_description(code: int, language: str = "en") -> str:
    """Return the WMO weather description for *code* in *language*.
    Falls back to English if the language or code is not found."""
    lang = language.lower().split("-")[0]   # accept e.g. "sv-SE" → "sv"
    table = _WMO_TRANSLATIONS.get(lang, _WMO_TRANSLATIONS["en"])
    description = table.get(code) or _WMO_TRANSLATIONS["en"].get(code)
    return description or f"Unknown ({code})"

# ── Wind-direction compass roses ─────────────────────────────────────────────

# 16-point compass  (each sector = 22.5°)
_COMPASS_16 = [
    "N", "NNE", "NE", "ENE",
    "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW",
    "W", "WNW", "NW", "NNW",
]

# 12-point compass  (each sector = 30°)
_COMPASS_12 = [
    "N", "NNE", "NE",
    "E", "ESE", "SE",
    "S", "SSW", "SW",
    "W", "WNW", "NW",
]


def degrees_to_compass(degrees: float, fmt: str) -> str | float:
    """
    Convert a 0-360° bearing to the requested format.

    fmt:
      "degrees"    → return the raw float unchanged
      "16-compass" → 16-point rose  (N, NNE, NE, ENE, E, …)
      "12-compass" → 12-point rose  (N, NNE, NE, E, …)
    """
    fmt = (fmt or "degrees").lower().strip()
    if fmt == "degrees":
        return degrees
    if fmt in ("16-compass", "16"):
        idx = round(degrees / (360 / 16)) % 16
        return _COMPASS_16[idx]
    if fmt in ("12-compass", "12"):
        idx = round(degrees / (360 / 12)) % 12
        return _COMPASS_12[idx]
    # fallback
    return degrees


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    """Load and return the YAML configuration file."""
    p = Path(path)
    if not p.exists():
        sys.exit(f"Config file not found: {path}")
    with p.open() as fh:
        cfg = yaml.safe_load(fh)
    if cfg is None:
        sys.exit("Config file is empty.")
    return cfg


def cfg_get(cfg: dict, *keys, default=None):
    """Safely navigate nested dict keys."""
    node = cfg
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k, default)
        if node is None:
            return default
    return node


# ─────────────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging(cfg: dict) -> logging.Logger:
    level_name = cfg_get(cfg, "logging", "level", default="INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if cfg_get(cfg, "logging", "log_to_file"):
        log_file = cfg_get(cfg, "logging", "log_file", default="weather_mqtt.log")
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    return logging.getLogger("weather_mqtt")


# ─────────────────────────────────────────────────────────────────────────────
# MQTT
# ─────────────────────────────────────────────────────────────────────────────

def build_mqtt_client(cfg: dict, log: logging.Logger) -> mqtt.Client:
    mc = cfg["mqtt"]
    client_id = mc.get("client_id", "weather_publisher")

    if _PAHO_V2:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    else:
        client = mqtt.Client(client_id=client_id)

    if mc.get("username"):
        client.username_pw_set(mc["username"], mc.get("password", ""))

    if mc.get("tls"):
        import ssl
        ca_cert = mc.get("ca_cert")
        if ca_cert:
            client.tls_set(ca_certs=ca_cert)
        else:
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)

    def on_connect(client, userdata, flags, *args):
        rc = args[0] if args else 0
        if rc == 0:
            log.info("MQTT connected to %s:%s", mc["broker"], mc.get("port", 1883))
        else:
            log.error("MQTT connection failed, rc=%s", rc)

    def on_disconnect(client, userdata, *args):
        # args[0] is the disconnect reason code; 0 means clean/intentional shutdown
        rc = args[0] if args else 0
        if rc != 0:
            log.warning("MQTT unexpectedly disconnected (rc=%s) – will reconnect …", rc)
        # rc == 0 is a clean disconnect triggered by us; no need to log

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    # Back off reconnect attempts: wait 5 s, max 30 s between retries
    client.reconnect_delay_set(min_delay=5, max_delay=30)
    return client


def mqtt_connect(client: mqtt.Client, cfg: dict, log: logging.Logger) -> bool:
    mc = cfg["mqtt"]
    broker = mc.get("broker", "localhost")
    port = int(mc.get("port", 1883))
    try:
        client.connect(broker, port, keepalive=60)
        # loop_start() runs the network loop in a background thread.
        # The connection stays open for the lifetime of the process; we never
        # call disconnect() except on clean shutdown, so on_disconnect only
        # fires for genuine broker-side drops.
        client.loop_start()
        # Wait up to 5 s for on_connect to confirm the connection.
        for _ in range(50):
            time.sleep(0.1)
            if client.is_connected():
                return True
        log.warning("MQTT connect timed out – will keep retrying in the background.")
        return True   # loop_start will keep retrying; don't abort the program
    except Exception as exc:
        log.error("Cannot connect to MQTT broker %s:%s – %s", broker, port, exc)
        return False


def publish_data(
    client: mqtt.Client,
    data: dict[str, Any],
    cfg: dict,
    log: logging.Logger,
    dry_run: bool,
) -> None:
    mc = cfg["mqtt"]
    retain = mc.get("retain", True)
    qos = int(mc.get("qos", 1))
    base = mc.get("base_topic", "home/weather").rstrip("/")
    per_field = mc.get("topic_per_field", True)

    if per_field:
        for field, value in data.items():
            topic = f"{base}/{field}"
            payload = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            if dry_run:
                log.info("[DRY-RUN] %s  →  %s", topic, payload)
            else:
                client.publish(topic, payload, qos=qos, retain=retain)
                log.debug("Published  %s  →  %s", topic, payload)
    else:
        topic = mc.get("json_topic", f"{base}/all")
        payload = json.dumps(data, ensure_ascii=False)
        if dry_run:
            log.info("[DRY-RUN] %s  →  %s", topic, payload)
        else:
            client.publish(topic, payload, qos=qos, retain=retain)
            log.info("Published JSON payload to %s", topic)


# ─────────────────────────────────────────────────────────────────────────────
# Open-Meteo fetcher
# ─────────────────────────────────────────────────────────────────────────────

# Mapping: config field → open-meteo variable name
_OM_CURRENT_MAP = {
    "temperature":   "temperature_2m",
    "feels_like":    "apparent_temperature",
    "humidity":      "relative_humidity_2m",
    "precipitation": "precipitation",
    "rain":          "rain",
    "wind_speed":    "wind_speed_10m",
    "wind_direction":"wind_direction_10m",
    "wind_gusts":    "wind_gusts_10m",
    "cloud_cover":   "cloud_cover",
    "visibility":    "visibility",
    "surface_pressure": "surface_pressure",
    "weather_code":  "weather_code",
    "is_day":        "is_day",
}

_OM_FORECAST_MAP = {
    "forecast_temperature_max":   "temperature_2m_max",
    "forecast_temperature_min":   "temperature_2m_min",
    "forecast_precipitation_sum": "precipitation_sum",
    "forecast_wind_speed_max":    "wind_speed_10m_max",
    "forecast_weather_code":      "weather_code",
}

def _om_wind_unit(unit: str) -> str:
    return {"kmh": "kmh", "mph": "mph", "ms": "ms", "knots": "kn"}.get(unit, "kmh")

def _om_temp_unit(unit: str) -> str:
    return "fahrenheit" if unit.lower() in ("f", "fahrenheit") else "celsius"

def _om_precip_unit(unit: str) -> str:
    return "inch" if unit.lower() in ("inch", "in") else "mm"


def fetch_open_meteo(cfg: dict, log: logging.Logger) -> dict[str, Any]:
    lang = cfg_get(cfg, "weather", "language", default="en")
    wc = cfg["weather"]
    lat = wc["latitude"]
    lon = wc["longitude"]
    units = wc.get("units", {})
    fields = wc.get("fields", {})

    current_vars = [
        om_var
        for cfg_key, om_var in _OM_CURRENT_MAP.items()
        if fields.get(cfg_key, False)
    ]
    # weather_description needs weather_code
    if fields.get("weather_description") and "weather_code" not in current_vars:
        current_vars.append("weather_code")

    if not current_vars:
        log.warning("No current fields enabled – nothing to fetch.")
        return {}

    params: dict[str, Any] = {
        "latitude":       lat,
        "longitude":      lon,
        "current":        ",".join(current_vars),
        "temperature_unit": _om_temp_unit(units.get("temperature", "celsius")),
        "wind_speed_unit":  _om_wind_unit(units.get("wind_speed", "kmh")),
        "precipitation_unit": _om_precip_unit(units.get("precipitation", "mm")),
        "timezone":       units.get("timezone", "auto"),
        "forecast_days":  1,
    }

    # Optional hourly forecast
    if fields.get("forecast_enabled"):
        hours = int(fields.get("forecast_hours", 24))
        hourly_vars = [
            om_var
            for cfg_key, om_var in _OM_FORECAST_MAP.items()
            if fields.get(cfg_key, False)
        ]
        if hourly_vars:
            params["hourly"] = ",".join(hourly_vars)
            params["forecast_days"] = max(1, (hours // 24) + 1)

    url = "https://api.open-meteo.com/v1/forecast"
    log.debug("Open-Meteo request: %s  params: %s", url, params)

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    raw = resp.json()

    current_raw = raw.get("current", {})
    result: dict[str, Any] = {}

    for cfg_key, om_var in _OM_CURRENT_MAP.items():
        if fields.get(cfg_key) and om_var in current_raw:
            result[cfg_key] = current_raw[om_var]

    # Convert wind direction to requested format
    if "wind_direction" in result:
        wd_fmt = units.get("wind_direction", "degrees")
        result["wind_direction"] = degrees_to_compass(result["wind_direction"], wd_fmt)

    if fields.get("weather_description"):
        code = int(current_raw.get("weather_code", -1))
        result["weather_description"] = wmo_description(code, lang)

    # Hourly forecast
    if fields.get("forecast_enabled") and "hourly" in raw:
        hourly = raw["hourly"]
        hours = int(fields.get("forecast_hours", 24))
        times = hourly.get("time", [])[:hours]
        forecast: list[dict] = []
        for i, t in enumerate(times):
            entry: dict[str, Any] = {"time": t}
            for cfg_key, om_var in _OM_FORECAST_MAP.items():
                if fields.get(cfg_key) and om_var in hourly:
                    entry[cfg_key.replace("forecast_", "")] = hourly[om_var][i]
                    if cfg_key == "forecast_weather_code":
                        code = int(hourly[om_var][i])
                        entry["weather_description"] = wmo_description(code, lang)
            forecast.append(entry)
        result["forecast"] = forecast

    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    result["source"] = "open-meteo"
    return result


# ─────────────────────────────────────────────────────────────────────────────
# wttr.in fetcher
# ─────────────────────────────────────────────────────────────────────────────

def fetch_wttr(cfg: dict, log: logging.Logger) -> dict[str, Any]:
    wc = cfg["weather"]
    units_cfg = wc.get("units", {})
    fields = wc.get("fields", {})
    location = wc.get("location_name", "")
    if not location:
        location = f"{wc['latitude']},{wc['longitude']}"

    temp_unit = units_cfg.get("temperature", "celsius")
    # wttr.in format strings: ?format=j1 returns JSON
    url = f"https://wttr.in/{requests.utils.quote(location)}"
    params = {"format": "j1"}
    if temp_unit.lower() in ("f", "fahrenheit"):
        params["m"] = ""   # metric flag off  (wttr uses imperial by default without m)
    else:
        params["m"] = ""   # metric

    log.debug("wttr.in request: %s  params: %s", url, params)
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    raw = resp.json()

    cc = raw.get("current_condition", [{}])[0]
    result: dict[str, Any] = {}

    wind_unit = units_cfg.get("wind_speed", "kmh")

    if fields.get("temperature"):
        key = "temp_F" if temp_unit.lower() in ("f", "fahrenheit") else "temp_C"
        result["temperature"] = float(cc.get(key, 0))

    if fields.get("feels_like"):
        key = "FeelsLikeF" if temp_unit.lower() in ("f", "fahrenheit") else "FeelsLikeC"
        result["feels_like"] = float(cc.get(key, 0))

    if fields.get("humidity"):
        result["humidity"] = float(cc.get("humidity", 0))

    if fields.get("precipitation"):
        result["precipitation"] = float(cc.get("precipMM", 0))

    if fields.get("wind_speed"):
        if wind_unit == "mph":
            result["wind_speed"] = float(cc.get("windspeedMiles", 0))
        else:
            result["wind_speed"] = float(cc.get("windspeedKmph", 0))

    if fields.get("wind_direction"):
        raw_deg = float(cc.get("winddirDegree", 0))
        wd_fmt = units_cfg.get("wind_direction", "degrees")
        result["wind_direction"] = degrees_to_compass(raw_deg, wd_fmt)
        # always include the raw 16-point label wttr gives us as a bonus field
        result["wind_direction_compass"] = cc.get("winddir16Point", "")

    if fields.get("cloud_cover"):
        result["cloud_cover"] = float(cc.get("cloudcover", 0))

    if fields.get("surface_pressure"):
        result["surface_pressure"] = float(cc.get("pressure", 0))

    if fields.get("visibility"):
        result["visibility"] = float(cc.get("visibility", 0))

    if fields.get("weather_code") or fields.get("weather_description"):
        code = int(cc.get("weatherCode", 0))
        if fields.get("weather_code"):
            result["weather_code"] = code
        if fields.get("weather_description"):
            lang = units_cfg.get("language",
                   cfg_get(cfg, "weather", "language", default="en"))
            translated = wmo_description(code, lang) if lang != "en" else None
            if translated:
                result["weather_description"] = translated
            else:
                # fall back to wttr.in's own description string
                desc_list = cc.get("weatherDesc", [{}])
                result["weather_description"] = desc_list[0].get("value", "") if desc_list else ""

    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    result["source"] = "wttr.in"
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def fetch_weather(cfg: dict, log: logging.Logger) -> dict[str, Any]:
    service = cfg_get(cfg, "weather", "service", default="open-meteo").lower()
    if service == "open-meteo":
        return fetch_open_meteo(cfg, log)
    elif service in ("wttr.in", "wttr"):
        return fetch_wttr(cfg, log)
    else:
        log.error("Unknown weather service '%s'. Use 'open-meteo' or 'wttr.in'.", service)
        return {}


def _countdown_sleep(seconds: int, step: int = 10) -> None:
    """
    Sleep for `seconds` total, printing a countdown on a single
    updating line (using carriage-return \r) every `step` seconds.
    The line is cleared cleanly when the countdown finishes.
    """
    remaining = seconds
    while remaining > 0:
        # \r returns to column 0 without newline; end="" suppresses the newline
        print(f"\r  Next fetch in {remaining:4d} s …", end="", flush=True)
        wait = min(step, remaining)
        time.sleep(wait)
        remaining -= wait
    # Overwrite the countdown line with spaces, then return to column 0
    print("\r" + " " * 30 + "\r", end="", flush=True)


def _publish_version(
    client,
    cfg: dict,
    log: logging.Logger,
    dry_run: bool,
) -> None:
    """Publish the script version string to its own MQTT topic once on startup."""
    mc = cfg["mqtt"]
    base = mc.get("base_topic", "home/weather").rstrip("/")
    topic = f"{base}/version"
    payload = __VERSION__
    if dry_run:
        log.info("[DRY-RUN] %s  →  %s", topic, payload)
    else:
        qos = int(mc.get("qos", 1))
        retain = mc.get("retain", True)
        client.publish(topic, payload, qos=qos, retain=retain)
        log.info("Version %s published to %s", payload, topic)


def run(cfg: dict, log: logging.Logger, once: bool, dry_run: bool) -> None:
    interval = int(cfg_get(cfg, "weather", "interval", default=300))

    if dry_run:
        log.info("DRY-RUN mode – MQTT will NOT be used.")
        client = None
    else:
        client = build_mqtt_client(cfg, log)
        if not mqtt_connect(client, cfg, log):
            sys.exit(1)

    # Publish script version once on startup
    _publish_version(client, cfg, log, dry_run)

    try:
        while True:
            log.info("Fetching weather data …")
            try:
                data = fetch_weather(cfg, log)
            except requests.RequestException as exc:
                log.error("Weather fetch failed: %s", exc)
                data = {}

            if data:
                log.info("Fetched %d field(s).", len(data))
                publish_data(client, data, cfg, log, dry_run)
            else:
                log.warning("No data to publish.")

            if once:
                break

            _countdown_sleep(interval)

    except KeyboardInterrupt:
        log.info("Interrupted by user.")
    finally:
        if client:
            client.loop_stop()
            client.disconnect()   # on_disconnect fires here with rc=0 → not logged
            log.info("MQTT connection closed.")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch weather and publish to MQTT.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config", default="config.yaml", metavar="FILE",
        help="Path to the YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Fetch once and exit (ignore interval setting)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print payloads to stdout without connecting to MQTT",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    log = setup_logging(cfg)

    log.info("weather_mqtt.py %s starting  (service=%s, interval=%ss)", __VERSION__,
             cfg_get(cfg, "weather", "service", default="open-meteo"),
             cfg_get(cfg, "weather", "interval", default=300))

    run(cfg, log, once=args.once, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
