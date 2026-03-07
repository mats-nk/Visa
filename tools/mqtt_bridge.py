#!/usr/bin/env python3
"""
mqtt_bridge.py
~~~~~~~~~~~~~~
Subscribe to MQTT topics on a source broker and republish the messages
to a destination broker (which may be the same broker).

All settings – including the topic mapping table – are read from
mqtt_bridge_config.yaml (or the path given with --config).

Usage:
    python mqtt_bridge.py                          # uses mqtt_bridge_config.yaml
    python mqtt_bridge.py --config my_bridge.yaml  # custom config path
    python mqtt_bridge.py --dry-run                # print forwards, skip publish
    python mqtt_bridge.py --list                   # print active mappings and exit

Dependencies:
    pip install paho-mqtt pyyaml
"""

__VERSION__ = "2026-03-07.10"   # format: YYYY-MM-DD.sequence

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

# ── paho-mqtt version detection ───────────────────────────────────────────────
try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("paho-mqtt is not installed.  Run:  pip install paho-mqtt pyyaml")

try:
    import paho
    _paho_ver = getattr(paho, "__version__", None)
    if _paho_ver is None:
        from importlib.metadata import version as _pkg_ver
        _paho_ver = _pkg_ver("paho-mqtt")
    _PAHO_V2 = int(_paho_ver.split(".")[0]) >= 2
except Exception:
    _PAHO_V2 = hasattr(mqtt, "CallbackAPIVersion")


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        sys.exit(f"Config file not found: {path}")
    with p.open() as fh:
        cfg = yaml.safe_load(fh)
    if not cfg:
        sys.exit("Config file is empty.")
    return cfg


def cfg_get(cfg: dict, *keys, default=None):
    node = cfg
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k, default)
        if node is None:
            return default
    return node


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging(cfg: dict) -> logging.Logger:
    level_name = cfg_get(cfg, "logging", "level", default="INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if cfg_get(cfg, "logging", "log_to_file"):
        log_file = cfg_get(cfg, "logging", "log_file", default="mqtt_bridge.log")
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    return logging.getLogger("mqtt_bridge")


# ─────────────────────────────────────────────────────────────────────────────
# Value transforms
# ─────────────────────────────────────────────────────────────────────────────

def apply_transform(value: str, transform: str | None, log: logging.Logger) -> str:
    """Apply a single named transform step and return the result."""
    if not transform:
        return value

    # Strip only leading/trailing whitespace from the whole transform string,
    # but when extracting arguments (suffix, prefix, template …) we split on
    # the FIRST colon only so spaces inside the argument are fully preserved.
    t = transform.strip()

    if t == "passthrough":
        return value

    if t == "upper":
        return value.upper()

    if t == "lower":
        return value.lower()

    if t.startswith("round:"):
        try:
            decimals = int(t.split(":")[1])
            return str(round(float(value), decimals))
        except (ValueError, IndexError) as exc:
            log.warning("transform 'round' failed (%s) – passing value through", exc)
            return value

    if t.startswith("scale:"):
        try:
            factor = float(t.split(":")[1])
            return str(float(value) * factor)
        except (ValueError, IndexError) as exc:
            log.warning("transform 'scale' failed (%s) – passing value through", exc)
            return value

    if t.startswith("offset:"):
        try:
            offset = float(t.split(":")[1])
            return str(float(value) + offset)
        except (ValueError, IndexError) as exc:
            log.warning("transform 'offset' failed (%s) – passing value through", exc)
            return value

    if t.startswith("json_field:"):
        try:
            field = t.split(":", 1)[1]
            parsed = json.loads(value)
            return str(parsed[field])
        except (json.JSONDecodeError, KeyError, IndexError) as exc:
            log.warning("transform 'json_field' failed (%s) – passing value through", exc)
            return value

    if t.startswith("template:"):
        try:
            template = t.split(":", 1)[1]
            return template.replace("{value}", value)
        except Exception as exc:
            log.warning("transform 'template' failed (%s) – passing value through", exc)
            return value

    if t.startswith("suffix:"):
        # Use the RAW (non-stripped) transform so trailing spaces are kept.
        # Only strip the leading whitespace before the keyword itself.
        suffix = transform.lstrip().split(":", 1)[1]
        return value + suffix

    if t.startswith("prefix:"):
        prefix = transform.lstrip().split(":", 1)[1]
        return prefix + value

    log.warning("Unknown transform '%s' – passing value through", t)
    return value


def apply_transforms(
    value: str,
    transform: "str | list | None",
    log: logging.Logger,
) -> str:
    """
    Apply one or more transforms in sequence.

    *transform* may be:
      - None / omitted   → value passed through unchanged
      - a string         → single transform applied  (e.g. "round:1")
      - a list of strings→ transforms applied left-to-right as a pipeline
                           (e.g. ["round:1", "template:{value} °C"])
    """
    if transform is None:
        return value
    steps = transform if isinstance(transform, list) else [transform]
    for step in steps:
        value = apply_transform(value, step, log)
    return value


# ─────────────────────────────────────────────────────────────────────────────
# Topic pattern helpers
# ─────────────────────────────────────────────────────────────────────────────

def mqtt_pattern_to_regex(pattern: str) -> re.Pattern:
    """Convert an MQTT topic pattern (with + and #) to a regex."""
    parts = pattern.split("/")
    regex_parts = []
    for part in parts:
        if part == "#":
            regex_parts.append("(.+)")   # matches one or more levels
            break
        elif part == "+":
            regex_parts.append("([^/]+)")
        else:
            regex_parts.append(re.escape(part))
    return re.compile("^" + "/".join(regex_parts) + "$")


def resolve_destination(dest_pattern: str, src_topic: str, src_pattern: str) -> str:
    """
    Replace {topic}, {0}, {1}, … placeholders in dest_pattern with the
    actual topic levels captured by the source wildcard pattern.
    """
    if "{" not in dest_pattern:
        return dest_pattern

    levels = src_topic.split("/")
    result = dest_pattern.replace("{topic}", src_topic)

    # Replace {N} with the N-th level of the source topic (0-indexed)
    for i, level in enumerate(levels):
        result = result.replace(f"{{{i}}}", level)

    # Also replace named captures from wildcard segments
    regex = mqtt_pattern_to_regex(src_pattern)
    m = regex.match(src_topic)
    if m:
        for i, group in enumerate(m.groups(), start=1):
            result = result.replace(f"{{{i}}}", group)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Mapping table
# ─────────────────────────────────────────────────────────────────────────────

class Mapping:
    """Represents one source→destination forwarding rule."""

    def __init__(self, raw: dict, global_retain: bool, global_qos: int):
        self.src = raw["from"]
        self.dst = raw["to"]
        self.enabled = raw.get("enabled", True)
        self.retain = raw.get("retain", global_retain)
        self.qos = int(raw.get("qos", global_qos))
        self.transform = raw.get("transform")
        self.is_wildcard = "+" in self.src or "#" in self.src
        self._regex = mqtt_pattern_to_regex(self.src) if self.is_wildcard else None

    def matches(self, topic: str) -> bool:
        if self.is_wildcard:
            return bool(self._regex.match(topic))
        return topic == self.src

    def destination_topic(self, src_topic: str) -> str:
        return resolve_destination(self.dst, src_topic, self.src)


class SequenceMapping:
    """
    Round-robin mapping: each heartbeat publishes the *next* source topic
    in the sequence to the same destination topic.

    Config example:
        - to: "house/temperature"
          sequence:
            - from: "sensor/temp_indoor"
            - from: "sensor/temp_outdoor"
          enabled: true
    """

    def __init__(self, raw: dict, global_retain: bool, global_qos: int):
        self.dst = raw["to"]
        self.enabled = raw.get("enabled", True)
        self.retain = raw.get("retain", global_retain)
        self.qos = int(raw.get("qos", global_qos))
        self.steps: list[dict] = raw["sequence"]   # list of {from, transform?}
        self._index = 0   # current position in the rotation

    def current_src(self) -> str:
        return self.steps[self._index]["from"]

    def current_transform(self):
        return self.steps[self._index].get("transform")

    def advance(self) -> None:
        """Move to the next step, wrapping around to 0 after the last."""
        self._index = (self._index + 1) % len(self.steps)


class RoundRobinGroup:
    """
    Groups multiple Mappings that share the same destination topic and
    publishes only one per heartbeat, rotating through them in order.

    Triggered automatically when two or more enabled mappings point to
    the same 'to' topic, OR explicitly with  round_robin: true  on each entry.
    """
    def __init__(self, dst: str, members: list):
        self.dst = dst
        self.members = members   # list of Mapping
        self._index = 0

    def current(self):
        return self.members[self._index]

    def advance(self):
        self._index = (self._index + 1) % len(self.members)


def load_sequence_mappings(cfg: dict, log: logging.Logger) -> list[SequenceMapping]:
    """Load sequence mappings (round-robin) from the config."""
    raw_list = cfg.get("mappings", [])
    global_retain = cfg_get(cfg, "destination", "retain", default=True)
    global_qos = int(cfg_get(cfg, "destination", "qos", default=1))

    seq_mappings = []
    for i, raw in enumerate(raw_list):
        if "sequence" not in raw:
            continue
        if not raw.get("to"):
            log.warning("Sequence mapping #%d missing 'to' – skipped.", i + 1)
            continue
        if not raw["sequence"]:
            log.warning("Sequence mapping #%d has empty sequence – skipped.", i + 1)
            continue
        sm = SequenceMapping(raw, global_retain, global_qos)
        if sm.enabled:
            seq_mappings.append(sm)
            sources = [s["from"] for s in sm.steps]
            log.debug("Loaded sequence mapping → %s  [%s]", sm.dst, ", ".join(sources))
        else:
            log.debug("Skipped disabled sequence mapping → %s", sm.dst)

    if seq_mappings:
        log.info("Loaded %d sequence mapping(s).", len(seq_mappings))
    return seq_mappings


def load_mappings(cfg: dict, log: logging.Logger) -> tuple:
    """
    Returns (normal_mappings, rr_groups) where:
      normal_mappings – Mapping objects with unique destinations (forwarded on message)
      rr_groups       – RoundRobinGroup objects (forwarded one-per-heartbeat, rotating)

    A mapping is treated as round-robin when:
      - It has  round_robin: true  explicitly, OR
      - Two or more enabled mappings share the same 'to' topic
    """
    raw_list = cfg.get("mappings", [])
    global_retain = cfg_get(cfg, "destination", "retain", default=True)
    global_qos = int(cfg_get(cfg, "destination", "qos", default=1))

    all_mappings = []
    for i, raw in enumerate(raw_list):
        if "sequence" in raw:
            continue   # handled by load_sequence_mappings
        if not raw.get("from") or not raw.get("to"):
            log.warning("Mapping #%d missing 'from' or 'to' – skipped.", i + 1)
            continue
        m = Mapping(raw, global_retain, global_qos)
        m.round_robin = raw.get("round_robin", False)
        if m.enabled:
            all_mappings.append(m)
            log.debug("Loaded mapping: %s  →  %s", m.src, m.dst)
        else:
            log.debug("Skipped disabled mapping: %s  →  %s", m.src, m.dst)

    # Count how many enabled mappings share each destination
    from collections import Counter
    dst_counts = Counter(m.dst for m in all_mappings)

    normal = []
    rr_buckets: dict[str, list] = {}

    for m in all_mappings:
        if m.round_robin or dst_counts[m.dst] > 1:
            rr_buckets.setdefault(m.dst, []).append(m)
        else:
            normal.append(m)

    rr_groups = []
    for dst, members in rr_buckets.items():
        grp = RoundRobinGroup(dst, members)
        rr_groups.append(grp)
        sources = [m.src for m in members]
        log.info("Round-robin group → %s  [%s]", dst, "  →  ".join(sources))

    log.info("Loaded %d normal mapping(s), %d round-robin group(s).", len(normal), len(rr_groups))
    return normal, rr_groups


# ─────────────────────────────────────────────────────────────────────────────
# MQTT client factory
# ─────────────────────────────────────────────────────────────────────────────

def build_client(section: dict, label: str, log: logging.Logger) -> mqtt.Client:
    client_id = section.get("client_id", f"mqtt_bridge_{label}")

    if _PAHO_V2:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    else:
        client = mqtt.Client(client_id=client_id)

    if section.get("username"):
        client.username_pw_set(section["username"], section.get("password", ""))

    if section.get("tls"):
        import ssl
        ca_cert = section.get("ca_cert")
        if ca_cert:
            client.tls_set(ca_certs=ca_cert)
        else:
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)

    client.reconnect_delay_set(min_delay=5, max_delay=30)

    def on_connect(c, userdata, flags, *args):
        rc = args[0] if args else 0
        if rc == 0:
            log.info("[%s] Connected to %s:%s", label, section["broker"], section.get("port", 1883))
        else:
            log.error("[%s] Connection failed, rc=%s", label, rc)

    def on_disconnect(c, userdata, *args):
        rc = args[0] if args else 0
        if rc != 0:
            log.warning("[%s] Unexpectedly disconnected (rc=%s) – will reconnect …", label, rc)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    return client


def connect_client(client: mqtt.Client, section: dict, label: str, log: logging.Logger) -> bool:
    broker = section.get("broker", "localhost")
    port = int(section.get("port", 1883))
    try:
        client.connect(broker, port, keepalive=60)
        client.loop_start()
        for _ in range(50):
            time.sleep(0.1)
            if client.is_connected():
                return True
        log.warning("[%s] Connect timed out – retrying in background.", label)
        return True
    except Exception as exc:
        log.error("[%s] Cannot connect to %s:%s – %s", label, broker, port, exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Bridge logic
# ─────────────────────────────────────────────────────────────────────────────

def make_on_message(
    dst_client: mqtt.Client,
    mappings: list[Mapping],
    log: logging.Logger,
    dry_run: bool,
    counters: dict,
):
    """Return an on_message callback that forwards matching messages."""

    def on_message(client, userdata, msg):
        src_topic = msg.topic
        value = msg.payload.decode("utf-8", errors="replace")

        for mapping in mappings:
            if not mapping.matches(src_topic):
                continue

            dst_topic = mapping.destination_topic(src_topic)
            transformed = apply_transforms(value, mapping.transform, log)

            if dry_run:
                log.info("[DRY-RUN] %s  →  %s  (value: %s)", src_topic, dst_topic, transformed)
            else:
                dst_client.publish(
                    dst_topic,
                    transformed,
                    qos=mapping.qos,
                    retain=mapping.retain,
                )
                log.debug("Forwarded  %s  →  %s  : %s", src_topic, dst_topic, transformed)

            counters["forwarded"] += 1

    return on_message


def _publish_sequence(
    dst_client,
    src_client,
    seq_mappings: list,
    cfg: dict,
    log: logging.Logger,
    dry_run: bool,
    counters: dict,
) -> None:
    """
    For each sequence mapping: subscribe briefly to the current source topic,
    wait for its retained/latest value, forward it to the destination, then
    advance the rotation index.
    """
    if not seq_mappings:
        return

    dst_cfg = cfg.get("destination", {})

    for sm in seq_mappings:
        src_topic = sm.current_src()
        received: list[str] = []

        # One-shot subscribe: grab the next message on this topic
        def _on_seq_msg(client, userdata, msg, _sm=sm, _recv=received):
            _recv.append(msg.payload.decode("utf-8", errors="replace"))

        src_client.subscribe(src_topic)
        src_client.message_callback_add(src_topic, _on_seq_msg)

        # Wait up to 2 s for a message to arrive
        deadline = time.time() + 2.0
        while not received and time.time() < deadline:
            time.sleep(0.05)

        src_client.message_callback_remove(src_topic)
        src_client.unsubscribe(src_topic)

        if not received:
            log.warning("Sequence mapping: no message received from %s within 2 s – skipping.", src_topic)
            sm.advance()
            continue

        value = received[0]
        transformed = apply_transforms(value, sm.current_transform(), log)

        if dry_run:
            log.info(
                "[DRY-RUN][SEQ %d/%d] %s  →  %s  (value: %s)",
                sm._index + 1, len(sm.steps), src_topic, sm.dst, transformed,
            )
        else:
            dst_client.publish(sm.dst, transformed, qos=sm.qos, retain=sm.retain)
            log.info(
                "[SEQ %d/%d] %s  →  %s  : %s",
                sm._index + 1, len(sm.steps), src_topic, sm.dst, transformed,
            )

        counters["forwarded"] += 1
        sm.advance()


def _publish_round_robin(
    dst_client,
    src_client,
    rr_groups: list,
    log: logging.Logger,
    dry_run: bool,
    counters: dict,
) -> None:
    """For each round-robin group, fetch the current member's retained value
    and publish it to the shared destination, then advance the index."""
    for grp in rr_groups:
        m = grp.current()
        received: list[str] = []

        def _on_rr_msg(client, userdata, msg, _recv=received):
            _recv.append(msg.payload.decode("utf-8", errors="replace"))

        src_client.subscribe(m.src)
        src_client.message_callback_add(m.src, _on_rr_msg)

        deadline = time.time() + 2.0
        while not received and time.time() < deadline:
            time.sleep(0.05)

        src_client.message_callback_remove(m.src)
        src_client.unsubscribe(m.src)

        if not received:
            log.warning("[RR] No message from %s within 2 s – skipping this step.", m.src)
            grp.advance()
            continue

        value = received[0]
        transformed = apply_transforms(value, m.transform, log)
        step = grp._index + 1
        total = len(grp.members)

        if dry_run:
            log.info("[DRY-RUN][RR %d/%d] %s  →  %s  : %s", step, total, m.src, grp.dst, transformed)
        else:
            dst_client.publish(grp.dst, transformed, qos=m.qos, retain=m.retain)
            log.info("[RR %d/%d] %s  →  %s  : %s", step, total, m.src, grp.dst, transformed)

        counters["forwarded"] += 1
        grp.advance()


def _publish_version(
    dst_client,
    cfg: dict,
    log: logging.Logger,
    dry_run: bool,
) -> None:
    """Publish the script version string to the configured version topic."""
    dst_cfg = cfg.get("destination", {})
    topic = cfg_get(cfg, "bridge", "version_topic", default="bridge/version")
    retain = dst_cfg.get("retain", True)
    qos = int(dst_cfg.get("qos", 1))
    if dry_run:
        log.info("[DRY-RUN] %s  →  %s", topic, __VERSION__)
    else:
        dst_client.publish(topic, __VERSION__, qos=qos, retain=retain)
        log.info("Version %s published to %s", __VERSION__, topic)


def _countdown_sleep(seconds: int, step: int = 10) -> None:
    """Sleep for *seconds* total, printing an in-place countdown every *step* seconds."""
    remaining = seconds
    while remaining > 0:
        print(f"\r  Next heartbeat in {remaining:4d} s …", end="", flush=True)
        wait = min(step, remaining)
        time.sleep(wait)
        remaining -= wait
    print("\r" + " " * 36 + "\r", end="", flush=True)


def run(cfg: dict, log: logging.Logger, dry_run: bool) -> None:
    mappings, rr_groups = load_mappings(cfg, log)
    seq_mappings = load_sequence_mappings(cfg, log)
    if not mappings and not rr_groups and not seq_mappings:
        log.error("No active mappings found – nothing to do.")
        sys.exit(1)

    src_cfg = cfg.get("source", {})
    dst_cfg = cfg.get("destination", {})

    # ── Destination client ────────────────────────────────────
    if dry_run:
        log.info("DRY-RUN mode – messages will be logged but NOT published.")
        dst_client = None
    else:
        dst_client = build_client(dst_cfg, "DST", log)
        if not connect_client(dst_client, dst_cfg, "DST", log):
            sys.exit(1)

    # ── Source client ─────────────────────────────────────────
    counters = {"forwarded": 0}
    src_client = build_client(src_cfg, "SRC", log)
    src_client.on_message = make_on_message(dst_client, mappings, log, dry_run, counters)

    def on_src_connect(client, userdata, flags, *args):
        rc = args[0] if args else 0
        if rc == 0:
            log.info("[SRC] Connected to %s:%s", src_cfg["broker"], src_cfg.get("port", 1883))
            # Subscribe to every unique source pattern (normal mappings only;
            # round-robin groups subscribe/unsubscribe per heartbeat)
            patterns = list({m.src for m in mappings})
            for pattern in patterns:
                client.subscribe(pattern)
                log.info("[SRC] Subscribed to  %s", pattern)
        else:
            log.error("[SRC] Connection failed, rc=%s", rc)

    def on_src_disconnect(client, userdata, *args):
        rc = args[0] if args else 0
        if rc != 0:
            log.warning("[SRC] Unexpectedly disconnected (rc=%s) – will reconnect …", rc)

    src_client.on_connect = on_src_connect
    src_client.on_disconnect = on_src_disconnect

    if not connect_client(src_client, src_cfg, "SRC", log):
        sys.exit(1)

    heartbeat = int(cfg_get(cfg, "bridge", "heartbeat_interval", default=60))

    # Publish version once on startup
    _publish_version(dst_client, cfg, log, dry_run)

    # Fire round-robin and sequence mappings once immediately on startup
    _publish_round_robin(dst_client, src_client, rr_groups, log, dry_run, counters)
    _publish_sequence(dst_client, src_client, seq_mappings, cfg, log, dry_run, counters)

    log.info(
        "Bridge running – %d normal, %d round-robin, %d sequence.  Press Ctrl-C to stop.",
        len(mappings), len(rr_groups), len(seq_mappings),
    )

    try:
        while True:
            _countdown_sleep(heartbeat)
            log.debug("Heartbeat – messages forwarded so far: %d", counters["forwarded"])
            _publish_version(dst_client, cfg, log, dry_run)
            _publish_round_robin(dst_client, src_client, rr_groups, log, dry_run, counters)
            _publish_sequence(dst_client, src_client, seq_mappings, cfg, log, dry_run, counters)
    except KeyboardInterrupt:
        log.info("Interrupted by user.  Total messages forwarded: %d", counters["forwarded"])
    finally:
        src_client.loop_stop()
        src_client.disconnect()
        if dst_client:
            dst_client.loop_stop()
            dst_client.disconnect()
        log.info("Bridge stopped.")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def list_mappings(cfg: dict) -> None:
    """Print the active mapping table and exit."""
    raw_list = cfg.get("mappings", [])

    normal   = [r for r in raw_list if "sequence" not in r]
    seq      = [r for r in raw_list if "sequence" in r]

    active_n   = [r for r in normal if r.get("enabled", True)]
    disabled_n = [r for r in normal if not r.get("enabled", True)]
    active_s   = [r for r in seq    if r.get("enabled", True)]
    disabled_s = [r for r in seq    if not r.get("enabled", True)]

    print(f"\nmqtt_bridge.py  v{__VERSION__}")
    print(f"{'─' * 60}")

    def fmt_transform(xf) -> str:
        if not xf:
            return ""
        if isinstance(xf, list):
            return "  [" + " → ".join(xf) + "]"
        return f"  [{xf}]"

    print(f"  Active mappings  ({len(active_n)}):")
    for r in active_n:
        xf = fmt_transform(r.get("transform"))
        print(f"    {r['from']:<35} →  {r['to']}{xf}")

    if active_s:
        print(f"\n  Active sequence mappings  ({len(active_s)}):")
        for r in active_s:
            print(f"    → {r['to']}")
            for i, step in enumerate(r["sequence"], 1):
                xf = fmt_transform(step.get("transform"))
                print(f"      [{i}] {step['from']}{xf}")

    if disabled_n or disabled_s:
        print(f"\n  Disabled mappings  ({len(disabled_n) + len(disabled_s)}):")
        for r in disabled_n:
            xf = fmt_transform(r.get("transform"))
            print(f"    {r['from']:<35} →  {r['to']}{xf}")
        for r in disabled_s:
            sources = ", ".join(s["from"] for s in r["sequence"])
            print(f"    [SEQ] {sources}  →  {r['to']}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Forward MQTT messages from one topic/broker to another.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config", default="mqtt_bridge_config.yaml", metavar="FILE",
        help="Path to the YAML config file (default: mqtt_bridge_config.yaml)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Log forwarded messages without publishing to destination",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Print active mappings and exit",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    log = setup_logging(cfg)

    if args.list:
        list_mappings(cfg)
        return

    log.info("mqtt_bridge.py %s starting", __VERSION__)
    run(cfg, log, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
