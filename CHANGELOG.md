# Changelog

All notable changes to Visa are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [2025-03-07.4] — 2025-03-07

### Added
- Home Assistant: IP address published as retained MQTT topic `<hostname>/ip` and exposed as a diagnostic sensor entity
- Home Assistant: Firmware version published as retained MQTT topic `<hostname>/firmware` and exposed as a diagnostic sensor entity
- Home Assistant: `stat_t` (state topic) added to Speed, Pause, Animation and Alignment discovery payloads — HA sliders and selects now reflect real device state immediately after connect
- Home Assistant: Device card now shows firmware version via the `sw` field in the discovery device block
- `publishCurrentState()` — publishes all current config values (on/off, intensity, speed, pause, anim, align, IP, firmware) with `retain=true` on every MQTT connect
- Shared `animNames[]` and `alignNames[]` string tables used by both MQTT callback and HA discovery — no duplication

### Changed
- Home Assistant Animation select now shows human-readable option names (`Scroll Left`, `Bounce`, `Blink` etc.) instead of raw integers
- Home Assistant Alignment select now shows human-readable option names (`Left`, `Center`, `Right`) instead of raw integers
- `MQTTcallback()` accepts both human-readable name strings (from HA) and numeric strings (direct MQTT) for `anim` and `align` topics — fully backwards compatible

---

## [2025-03-07.3] — 2025-03-07

### Added
- Home Assistant MQTT Discovery: auto-creates six entities (Message text, Display light, Scroll Speed number, Pause number, Animation select, Alignment select)
- Availability topic `<hostname>/active` — HA entities show unavailable when device is offline
- `pauseMs` config field — hold display for configurable duration after each message pass
- `alignText` config field — left / centre / right alignment for short static messages
- 13 animation modes: Scroll Left/Right/Up/Down, Flip LR/UD, Rotate CW, Invert, Bounce, Scroll Left Inv, Blink, Wipe In, Wipe Out
- `ANIM_BOUNCE` ping-pong scroll mode
- `ANIM_BLINK` whole-display blink mode
- `ANIM_WIPE_IN` / `ANIM_WIPE_OUT` column wipe modes
- `displayStatic()` renderer for short messages with alignment support
- `messagePixelWidth()` helper for short/long message detection
- Config topics for auxiliary pin (`config/auxpin`) and heartbeat LED pin (`config/hbledpin`)
- Fixed rename topic `matrix/config/hostname` — any device can be renamed before its hostname is known

### Changed
- Scroll state machine extended with `S_PASS_END` state to support end-of-pass pause
- `scrollReset` flag added for clean state machine restart when switching static↔scroll

---

## [2025-03-07.1] — 2025-03-07

### Added
- Initial release
- ESP8266 / ESP32 / ESP32-S2 / ESP32-C3 multi-architecture support
- WiFiManager captive portal for first-boot WiFi + MQTT broker provisioning
- ArduinoOTA with mDNS — flash over WiFi via `<hostname>.local`
- MQTT-driven message display with UTF-8 → Latin-1 transcoding
- Swedish character support: `å ä ö Å Ä Ö` and degree symbol `°`
- Scroll left animation via MD_MAX72XX transform callbacks
- EEPROM persistence for all config fields
- Per-architecture default SPI pin definitions
- `DEBUG` and `LED_HEARTBEAT` compile-time flags
- Config status topic `<hostname>/config/status` published on every connect
- Hardware reconfigure via MQTT without reboot (hwtype, numdevices, clkpin, datapin, cspin)
- `FW_VERSION` constant published in status topic

---
