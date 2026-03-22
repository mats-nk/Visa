#include <ArduinoOTA.h>

// WiFi header differs between architectures
#if defined(ARDUINO_ARCH_ESP8266)
  #include <ESP8266WiFi.h>
#elif defined(ARDUINO_ARCH_ESP32)
  #include <WiFi.h>
#else
  #error "Unsupported platform – please add a WiFi include for your target."
#endif
#include <PubSubClient.h>
#include <WiFiManager.h>   // https://github.com/tzapu/WiFiManager
#include <MD_MAX72xx.h>
#include <SPI.h>
#include <EEPROM.h>

// ─── Firmware Version ────────────────────────────────────────────────────────
// Format: YYYY-MM-DD.N  where N is a sequential build number for that date.
#define FW_VERSION "2025-03-07.3"
#define PRINT_CALLBACK  0
#define DEBUG           0
#define LED_HEARTBEAT   0

#if DEBUG
  #define PRINT(s, v)  { Serial.print(F(s)); Serial.print(v); }
  #define PRINTS(s)    { Serial.print(F(s)); }
#else
  #define PRINT(s, v)
  #define PRINTS(s)
#endif

#if LED_HEARTBEAT
  #define HB_LED_TIME  500
#endif

// ─── EEPROM Layout ───────────────────────────────────────────────────────────
// We store a config struct at address 0.
// Magic bytes guard against reading garbage on first boot.
#define EEPROM_SIZE   sizeof(MatrixConfig)
#define EEPROM_MAGIC  0xA5

struct MatrixConfig {
  uint8_t  magic;          // 0xA5 = valid
  uint8_t  hwType;         // MD_MAX72XX::moduleType_t cast to uint8_t
  uint8_t  numDevices;     // number of chained MAX7219 modules (1–8)
  uint8_t  clkPin;         // GPIO number for CLK
  uint8_t  dataPin;        // GPIO number for DATA / MOSI
  uint8_t  csPin;          // GPIO number for CS / SS
  uint8_t  auxPin;         // GPIO number for auxiliary output (e.g. LDR enable)
  uint8_t  hbLedPin;       // GPIO number for heartbeat LED
  uint16_t scrollDelay;    // ms between scroll steps
  uint8_t  intensity;      // 0–15
  uint8_t  animMode;       // animation mode — see ANIM_* constants
  uint16_t pauseMs;        // ms to hold display after each full message pass (0 = no pause)
  uint8_t  alignText;      // text alignment for short messages: ALIGN_LEFT / CENTER / RIGHT
  char     hostname[32];   // OTA + MQTT client hostname (max 31 chars + null)
  char     mqttServer[40]; // MQTT broker IP or hostname (max 39 chars + null)
  uint16_t mqttPort;       // MQTT broker port (default 1883)
};

// ─── Per-architecture SPI pin defaults ───────────────────────────────────────
// All values are GPIO numbers (not Arduino D-pin aliases) so they compile on
// every supported target.  Override via MQTT after first boot if your wiring
// differs from the defaults below.
//
//  Board / module           CLK        MOSI       CS
//  ──────────────────────  ────────   ────────   ────────
//  ESP8266 (D1 Mini         14 (D5)    13 (D7)    12 (D6)
//  ESP32 DevKit             18         23          5
//  ESP32-S2 Saola-1         36         35         34
//  ESP32-C3 XIAO / DevKit    6          7         20
#if defined(ARDUINO_ARCH_ESP8266)
  #define DEFAULT_PIN_CLK   14     // D5
  #define DEFAULT_PIN_DATA  13     // D7
  #define DEFAULT_PIN_CS    12     // D6
  #define DEFAULT_PIN_AUX    3     // GPIO3 = D0 on D1 Mini / NodeMCU
  #define DEFAULT_PIN_HB_LED 2     // GPIO2 = D4, onboard LED on most ESP8266 boards
#elif defined(CONFIG_IDF_TARGET_ESP32S2)
  #define DEFAULT_PIN_CLK   36     // 
  #define DEFAULT_PIN_DATA  35     // 
  #define DEFAULT_PIN_CS    34     // 
  #define DEFAULT_PIN_AUX    1     // GPIO1 — free on most ESP32-S2 boards
  #define DEFAULT_PIN_HB_LED 2     // onboard LED on Saola-1 / FeatherS2
#elif defined(CONFIG_IDF_TARGET_ESP32C3)
  #define DEFAULT_PIN_CLK    6     // 
  #define DEFAULT_PIN_DATA   7     // 
  #define DEFAULT_PIN_CS    20     // 
  #define DEFAULT_PIN_AUX    1     // GPIO1 — free on XIAO C3
  #define DEFAULT_PIN_HB_LED 8     // onboard WS2812 data pin on XIAO C3 (use a plain GPIO for a simple LED)
#elif defined(ARDUINO_ARCH_ESP32)  // plain ESP32 — must come after S2/C3
  #define DEFAULT_PIN_CLK   18     // 
  #define DEFAULT_PIN_DATA  23     // 
  #define DEFAULT_PIN_CS     5     // 
  #define DEFAULT_PIN_AUX    1     // GPIO1 (TX) — repurpose only if Serial is unused
  #define DEFAULT_PIN_HB_LED 2     // onboard LED on most ESP32 DevKit boards
#else
  #error "No default SPI pins defined for this target — add them above."
#endif

// ─── Other tuneable defaults ─────────────────────────────────────────────────
#define DEFAULT_HW_TYPE      ((uint8_t)MD_MAX72XX::FC16_HW)
#define DEFAULT_NUM_DEVICES  4             // 1–8; each MAX7219 drives 8 columns, so 4 = 32 columns total
#define DEFAULT_SCROLL_DELAY 100           // ms — lower = faster scroll
#define DEFAULT_INTENSITY    5             // 0 (off) … 15 (max)
#define DEFAULT_HOSTNAME     "LED_MATRIX"  // default hostname for OTA + MQTT (max 31 chars)
#define DEFAULT_MQTT_SERVER  ""            // blank — forces portal entry on first boot
#define DEFAULT_MQTT_PORT    1883          // default MQTT port

// ─── Animation modes ──────────────────────────────────────────────────────────
// Sent as integer over MQTT topic /<hostname>/anim
//
//  Value  Name              Description
//  ────────────────────────────────────────────────────────────────────────────
//  0   SCROLL_LEFT      Continuous scroll right→left            (default)
//  1   SCROLL_RIGHT     Continuous scroll left→right
//  2   SCROLL_UP        Continuous scroll downward→up
//  3   SCROLL_DOWN      Continuous scroll upward→down
//  4   FLIP_LR          Static display, mirrored left↔right
//  5   FLIP_UD          Static display, mirrored top↔bottom
//  6   ROTATE_CW        Rotate display 90° clockwise each tick
//  7   INVERT           Invert all pixels (dark↔light) each tick
//  8   BOUNCE           Scroll left until end, then reverse right (ping-pong)
//  9   SCROLL_LEFT_INV  Scroll left with pixels inverted
//  10  BLINK            Whole display blinks at scroll speed
//  11  WIPE_IN_LR       Wipe columns in from both sides toward centre
//  12  WIPE_OUT_LR      Wipe columns out from centre toward both sides
//
// pauseMs (MQTT topic /<hostname>/pause, 0 = disabled):
//   After each full message pass, hold the display for pauseMs milliseconds
//   before starting the next pass.  Works with all scroll modes.
#define ANIM_SCROLL_LEFT     0
#define ANIM_SCROLL_RIGHT    1
#define ANIM_SCROLL_UP       2
#define ANIM_SCROLL_DOWN     3
#define ANIM_FLIP_LR         4
#define ANIM_FLIP_UD         5
#define ANIM_ROTATE_CW       6
#define ANIM_INVERT          7
#define ANIM_BOUNCE          8
#define ANIM_SCROLL_LEFT_INV 9
#define ANIM_BLINK           10
#define ANIM_WIPE_IN         11
#define ANIM_WIPE_OUT        12
#define ANIM_MODE_MAX        12       // 

#define DEFAULT_ANIM_MODE  ANIM_SCROLL_LEFT
#define DEFAULT_PAUSE_MS    0         // 0 = no pause between passes

// Text alignment for short messages (rendered width ≤ display width).
// Sent as integer over MQTT topic /<hostname>/align
#define ALIGN_LEFT   0   // Flush left  (default)
#define ALIGN_CENTER 1   // Horizontally centred
#define ALIGN_RIGHT  2   // Flush right
#define ALIGN_MAX    2

#define DEFAULT_ALIGN_TEXT ALIGN_LEFT

// ─── Runtime config (loaded from EEPROM; falls back to defaults on first boot)
// Plain aggregate initialisation is used here — C99 designated initialisers
// (.field = value) are a GCC extension that the ESP toolchain rejects in C++.
MatrixConfig cfg = {
  EEPROM_MAGIC,          // magic
  DEFAULT_HW_TYPE,       // hwType
  DEFAULT_NUM_DEVICES,   // numDevices
  DEFAULT_PIN_CLK,       // clkPin
  DEFAULT_PIN_DATA,      // dataPin
  DEFAULT_PIN_CS,        // csPin
  DEFAULT_PIN_AUX,       // auxPin
  DEFAULT_PIN_HB_LED,    // hbLedPin
  DEFAULT_SCROLL_DELAY,  // scrollDelay
  DEFAULT_INTENSITY,     // intensity
  DEFAULT_ANIM_MODE,     // animMode
  DEFAULT_PAUSE_MS,      // pauseMs
  DEFAULT_ALIGN_TEXT,    // alignText
  DEFAULT_HOSTNAME,      // hostname
  DEFAULT_MQTT_SERVER,   // mqttServer
  DEFAULT_MQTT_PORT      // mqttPort
};

// ─── MQTT Topics (built dynamically from cfg.hostname) ───────────────────────
// Topic pattern:  /<hostname>/<suffix>
// e.g. hostname "LOBBY" → "/LOBBY/value", "/LOBBY/config/hwtype", etc.
//
// The hostname config topic is always fixed to "matrix/config/hostname"
// so any device can be renamed regardless of its current hostname.
#define TOPIC_MAX 80  // enough for "homeassistant/number/<31-char-hostname>/speed/config\0"

struct Topics {
  char pup_alive        [TOPIC_MAX];  // Published with retain=true on startup to signal presence; retained until power off
  char sub_value        [TOPIC_MAX];  // Subscribes to new message text
  char sub_speed        [TOPIC_MAX];  // Scroll speed (ms between steps)
  char sub_intensity    [TOPIC_MAX];  // Display intensity (0–15)
  char sub_OnOff        [TOPIC_MAX];  // Display on/off (true/false)
  char sub_anim         [TOPIC_MAX];  // Animation mode
  char sub_pause        [TOPIC_MAX];  // Pause duration between passes (ms)
  char sub_align        [TOPIC_MAX];  // Text alignment: 0=left 1=center 2=right
  char sub_cfg_hwtype   [TOPIC_MAX];  // Hardware type (MD_MAX72XX::moduleType_t cast to uint8_t)
  char sub_cfg_numdev   [TOPIC_MAX];  // Number of chained devices
  char sub_cfg_clkpin   [TOPIC_MAX];  // Clock pin
  char sub_cfg_datapin  [TOPIC_MAX];  // Data pin
  char sub_cfg_cspin    [TOPIC_MAX];  // Chip select pin
  char sub_cfg_auxpin   [TOPIC_MAX];  // Auxiliary output pin (e.g. for LDR control)
  char sub_cfg_hbledpin [TOPIC_MAX];  // Heartbeat LED pin
  char pub_cfg_status   [TOPIC_MAX];  // Publishes current config as JSON to this topic on every change
  char sub_cfg_hostname [TOPIC_MAX];  // Fixed — always "matrix/config/hostname"

  // Home Assistant MQTT discovery topics (publish with retain=true)
  // Pattern: homeassistant/<component>/<hostname>/<object>/config
  char ha_text     [TOPIC_MAX];       // Text entity   — message input
  char ha_light    [TOPIC_MAX];       // Light entity  — on/off + brightness
  char ha_speed    [TOPIC_MAX];       // Number entity — scroll speed
  char ha_pause    [TOPIC_MAX];       // Number entity — pause between passes
  char ha_anim     [TOPIC_MAX];       // Select entity — animation mode
  char ha_align    [TOPIC_MAX];       // Select entity — text alignment
} t;

// Call after eepromLoad() and after every hostname change.
void buildTopics() {
  const char* h = cfg.hostname;
  snprintf(t.pup_alive,        TOPIC_MAX, "%s/active",             h);
  snprintf(t.sub_value,        TOPIC_MAX, "%s/value",              h);
  snprintf(t.sub_speed,        TOPIC_MAX, "%s/speed",              h);
  snprintf(t.sub_intensity,    TOPIC_MAX, "%s/intensity",          h);
  snprintf(t.sub_OnOff,        TOPIC_MAX, "%s/state",              h);
  snprintf(t.sub_anim,         TOPIC_MAX, "%s/anim",               h);
  snprintf(t.sub_pause,        TOPIC_MAX, "%s/pause",              h);
  snprintf(t.sub_align,        TOPIC_MAX, "%s/align",              h);
  snprintf(t.sub_cfg_hwtype,   TOPIC_MAX, "%s/config/hwtype",      h);
  snprintf(t.sub_cfg_numdev,   TOPIC_MAX, "%s/config/numdevices",  h);
  snprintf(t.sub_cfg_clkpin,   TOPIC_MAX, "%s/config/clkpin",      h);
  snprintf(t.sub_cfg_datapin,  TOPIC_MAX, "%s/config/datapin",     h);
  snprintf(t.sub_cfg_cspin,    TOPIC_MAX, "%s/config/cspin",       h);
  snprintf(t.sub_cfg_auxpin,   TOPIC_MAX, "%s/config/auxpin",      h);
  snprintf(t.sub_cfg_hbledpin, TOPIC_MAX, "%s/config/hbledpin",    h);
  snprintf(t.pub_cfg_status,   TOPIC_MAX, "%s/config/status",      h);
  // Fixed rename topic — literal "matrix" prefix so every device can be
  // targeted for renaming before its hostname is known.
  strncpy(t.sub_cfg_hostname, "matrix/config/hostname", TOPIC_MAX - 1);
  t.sub_cfg_hostname[TOPIC_MAX - 1] = '\0';

  // Home Assistant (HA) discovery topics — 64 chars is tight;
  // HA prefix + component + hostname + object + /config
  // Use a longer local buffer here since TOPIC_MAX is for runtime topics only.
  snprintf(t.ha_text,  TOPIC_MAX, "homeassistant/text/%s/message/config",   h);
  snprintf(t.ha_light, TOPIC_MAX, "homeassistant/light/%s/display/config",  h);
  snprintf(t.ha_speed, TOPIC_MAX, "homeassistant/number/%s/speed/config",   h);
  snprintf(t.ha_pause, TOPIC_MAX, "homeassistant/number/%s/pause/config",   h);
  snprintf(t.ha_anim,  TOPIC_MAX, "homeassistant/select/%s/anim/config",    h);
  snprintf(t.ha_align, TOPIC_MAX, "homeassistant/select/%s/align/config",   h);

  Serial.printf("Topics built for hostname: %s\n", h);
}

// ─── Runtime State ───────────────────────────────────────────────────────────
#define OFF false
#define ON  true

bool   matrix_state         = true;
bool   intensity_update     = false;
bool   hardware_reconfigure = false;   // Set true when a config topic arrives
bool   firstConnect         = true;    // Show "WiFi+MQTT OK" on first MQTT connect only
bool   passComplete         = false;   // Set by scrollDataSource at end of each message pass
bool   scrollReset          = false;   // Set to force scrollDataSource back to S_IDLE cleanly

const uint8_t MESG_SIZE    = 255;
const uint8_t CHAR_SPACING = 1;

char curMessage[MESG_SIZE];
char newMessage[MESG_SIZE];
bool newMessageAvailable    = false;

long clearTimer = 0;

// ─── Forward declarations ────────────────────────────────────────────────────
// Required because matrixInit() registers these callbacks before their
// definitions appear later in the file.
void    scrollDataSink(uint8_t dev, MD_MAX72XX::transformType_t t, uint8_t col);
uint8_t scrollDataSource(uint8_t dev, MD_MAX72XX::transformType_t t);

// ─── Hardware Objects (rebuilt on reconfigure) ───────────────────────────────
MD_MAX72XX* mx = nullptr;   // Heap-allocated so we can delete/recreate it

WiFiClient   espClient;
PubSubClient client(espClient);

// ─── EEPROM helpers ──────────────────────────────────────────────────────────
void eepromLoad() {
  EEPROM.begin(EEPROM_SIZE);
  MatrixConfig tmp;
  EEPROM.get(0, tmp);
  if (tmp.magic == EEPROM_MAGIC) {
    cfg = tmp;
    Serial.println(F("Config loaded from EEPROM."));
  } else {
    Serial.println(F("No valid EEPROM config – using defaults."));
  }
  EEPROM.end();
}

void eepromSave() {
  EEPROM.begin(EEPROM_SIZE);
  cfg.magic = EEPROM_MAGIC;
  EEPROM.put(0, cfg);
  EEPROM.commit();
  EEPROM.end();
  Serial.println(F("Config saved to EEPROM."));
}

// ─── Matrix (re)initialisation ───────────────────────────────────────────────
void matrixInit() {
  if (mx) {
    mx->clear();
    delete mx;
    mx = nullptr;
  }

  // MD_MAX72XX does not expose a destructor that releases the SPI bus on
  // ESP8266, but re-constructing with the same pins is safe here.
  mx = new MD_MAX72XX(
    (MD_MAX72XX::moduleType_t)cfg.hwType,
    cfg.csPin,          // SW SPI: use HW SPI constructor (CS only)
    cfg.numDevices
  );

  mx->begin();
  mx->setShiftDataInCallback(scrollDataSource);
  mx->setShiftDataOutCallback(scrollDataSink);
  mx->control(MD_MAX72XX::INTENSITY, cfg.intensity);

  curMessage[0] = newMessage[0] = '\0';
  Serial.printf("Matrix init: hwType=%d devices=%d CS=%d\n",
                cfg.hwType, cfg.numDevices, cfg.csPin);
}

// ─── Swedish glyph table ──────────────────────────────────────────────────────
// MD_MAX72XX has no runtime API to inject bitmaps for arbitrary codepoints.
// Instead we intercept getChar() calls for the six Latin-1 Swedish codepoints
// and return our own 5-column bitmaps directly.
//
// Column bitmaps are 8 bits tall, LSB = top row, stored left→right.
// Designed at 5 columns wide to match the default font proportions.
//
//  å 0xE5   ä 0xE4   ö 0xF6   Å 0xC5   Ä 0xC4   Ö 0xD6   ° 0xB0
struct SwedishGlyph { uint8_t code; uint8_t cols[5]; };
static const SwedishGlyph swedishGlyphs[] PROGMEM = {
  // å — base 'a' (0x20,0x54,0x54,0x54,0x78) + small ring in rows 0-1 above
  //     ring: outer cols get bit1, inner cols get bit0
  { 0xE5, { 0x22, 0x55, 0x55, 0x56, 0x78 } },
  // ä — base 'a' + diaeresis dots (bit1) at cols 1 and 3
  { 0xE4, { 0x20, 0x56, 0x54, 0x56, 0x78 } },
  // ö — base 'o' (0x38,0x44,0x44,0x44,0x38) + diaeresis dots (bit1) at cols 1 and 3
  { 0xF6, { 0x38, 0x46, 0x44, 0x46, 0x38 } },
  // Å — capital 'A' with ring above
  { 0xC5, { 0x7C, 0x12, 0x11, 0x12, 0x7C } },
  // Ä — capital 'A' with diaeresis
  { 0xC4, { 0x7D, 0x12, 0x11, 0x12, 0x7D } },
  // Ö — capital 'O' with diaeresis
  { 0xD6, { 0x39, 0x44, 0x44, 0x44, 0x39 } },
  // ° — degree sign: small circle in top of cell
  //     col pattern: .XX. / X..X / X..X / .XX. / ....
  //     bits (LSB=top row): 0x06=00000110, 0x09=00001001
  { 0xB0, { 0x06, 0x09, 0x09, 0x06, 0x00 } },
};
static const uint8_t SWEDISH_GLYPH_COUNT =
  sizeof(swedishGlyphs) / sizeof(swedishGlyphs[0]);

// Drop-in replacement for mx->getChar().
// Checks the Swedish table first; falls through to the library font otherwise.
uint8_t getCharBitmap(uint8_t c, uint8_t size, uint8_t* buf) {
  for (uint8_t i = 0; i < SWEDISH_GLYPH_COUNT; i++) {
    if (pgm_read_byte(&swedishGlyphs[i].code) == c) {
      uint8_t w = min((uint8_t)5, size);
      for (uint8_t j = 0; j < w; j++)
        buf[j] = pgm_read_byte(&swedishGlyphs[i].cols[j]);
      return w;
    }
  }
  return mx->getChar(c, size, buf);
}

// ─── OTA ─────────────────────────────────────────────────────────────────────
void OTA_setup() {
  ArduinoOTA.setPort(8266);
  ArduinoOTA.setHostname(cfg.hostname);   // set from persisted config
  ArduinoOTA.setPassword("admin");
  ArduinoOTA.onStart([]()  { Serial.println("OTA Start"); });
  ArduinoOTA.onEnd([]()    { Serial.println("\nOTA End"); });
  ArduinoOTA.onProgress([](unsigned int p, unsigned int t) {
    Serial.printf("OTA Progress: %u%%\r", p / (t / 100));
  });
  ArduinoOTA.onError([](ota_error_t e) {
    Serial.printf("OTA Error[%u]: ", e);
    if      (e == OTA_AUTH_ERROR)    Serial.println("Auth Failed");
    else if (e == OTA_BEGIN_ERROR)   Serial.println("Begin Failed");
    else if (e == OTA_CONNECT_ERROR) Serial.println("Connect Failed");
    else if (e == OTA_RECEIVE_ERROR) Serial.println("Receive Failed");
    else if (e == OTA_END_ERROR)     Serial.println("End Failed");
  });
  ArduinoOTA.begin();
  Serial.print(F("OTA ready – IP: "));
  Serial.println(WiFi.localIP());
}

// ─── Scroll callbacks ────────────────────────────────────────────────────────
void scrollDataSink(uint8_t dev, MD_MAX72XX::transformType_t t, uint8_t col) {
#if PRINT_CALLBACK
  Serial.printf("\ncb dev=%d t=%d col=%d", dev, t, col);
#endif
}

uint8_t scrollDataSource(uint8_t dev, MD_MAX72XX::transformType_t t) {
  // States:
  //   S_IDLE       — reset pointer and begin rendering curMessage
  //   S_NEXT_CHAR  — fetch next character or start trailing gap
  //   S_SHOW_CHAR  — clock out character columns
  //   S_SHOW_SPACE — clock out inter-character spacing OR trailing full-width gap
  //   S_PASS_END   — hold (blank) until scrollText() clears passComplete,
  //                  then load any queued newMessage before starting next pass
  static enum { S_IDLE, S_NEXT_CHAR, S_SHOW_CHAR, S_SHOW_SPACE, S_PASS_END }
    state = S_IDLE;
  static char*    p;
  static uint16_t curLen, showLen;
  static uint8_t  cBuf[8];
  static bool     trailingGap = false;
  uint8_t colData = 0;

  // Hard reset — scrollText() sets this when transitioning from static→scroll
  // to ensure the state machine starts cleanly from S_IDLE.
  if (scrollReset) {
    scrollReset = false;
    state       = S_IDLE;
    trailingGap = false;
    passComplete = false;
  }

  switch (state) {

    case S_IDLE:
      // Only resets the read pointer — never touches newMessage here.
      // New messages are consumed exclusively in S_PASS_END after a full pass.
      PRINTS("\nS_IDLE");
      p           = curMessage;
      trailingGap = false;
      state       = S_NEXT_CHAR;
      break;

    case S_NEXT_CHAR:
      PRINTS("\nS_NEXT_CHAR");
      if (*p == '\0') {
        // All characters done — emit full-width trailing blank so the last
        // character scrolls completely off before signalling pass complete.
        showLen     = (uint16_t)(cfg.numDevices * COL_SIZE);
        curLen      = 0;
        trailingGap = true;
        state       = S_SHOW_SPACE;
      } else {
        showLen = getCharBitmap(*p++, sizeof(cBuf) / sizeof(cBuf[0]), cBuf);
        curLen  = 0;
        state   = S_SHOW_CHAR;
      }
      break;

    case S_SHOW_CHAR:
      PRINTS("\nS_SHOW_CHAR");
      colData = cBuf[curLen++];
      if (curLen < showLen) break;
      showLen = CHAR_SPACING;
      curLen  = 0;
      state   = S_SHOW_SPACE;
      // fall through

    case S_SHOW_SPACE:
      PRINT("\nS_ICSPACE: ", curLen);
      PRINT("/", showLen);
      curLen++;
      if (curLen == showLen) {
        if (trailingGap) {
          passComplete = true;
          state        = S_PASS_END;
        } else {
          state = S_NEXT_CHAR;
        }
      }
      break;

    case S_PASS_END:
      // Hold (emit blank) until scrollText() acknowledges by clearing passComplete.
      // Once released, swap in any queued message then go to S_IDLE.
      PRINTS("\nS_PASS_END");
      if (!passComplete) {
        if (newMessageAvailable) {
          strcpy(curMessage, newMessage);
          newMessageAvailable = false;
        }
        state = S_IDLE;
      }
      colData = 0;
      break;

    default:
      state = S_IDLE;
  }
  return colData;
}

// ─── Message width helper ─────────────────────────────────────────────────────
// Returns the rendered pixel-column width of msg (chars + inter-char spacing).
uint16_t messagePixelWidth(const char* msg) {
  uint16_t width = 0;
  uint8_t  cBuf[8];
  const char* p = msg;
  while (*p) {
    width += getCharBitmap(*p++, sizeof(cBuf) / sizeof(cBuf[0]), cBuf);
    if (*p) width += CHAR_SPACING;
  }
  return width;
}

// ─── Static message renderer ──────────────────────────────────────────────────
// Renders msg into the display buffer without scrolling.
// Alignment is controlled by cfg.alignText:
//   ALIGN_LEFT   — flush to left edge  (default)
//   ALIGN_CENTER — horizontally centred
//   ALIGN_RIGHT  — flush to right edge
// FC16 column 0 is physically the rightmost LED column, so we reverse the
// write order to match the scroll path's left-to-right visual direction.
void displayStatic(const char* msg) {
  uint8_t  cBuf[8];
  uint8_t  totalCols = cfg.numDevices * COL_SIZE;
  mx->clear();

  // Build flat column bitmap for the whole message
  static uint8_t colBuf[256];
  uint16_t msgWidth = 0;
  const char* q = msg;
  while (*q && msgWidth < sizeof(colBuf)) {
    uint8_t w = getCharBitmap(*q++, sizeof(cBuf) / sizeof(cBuf[0]), cBuf);
    for (uint8_t i = 0; i < w && msgWidth < sizeof(colBuf); i++)
      colBuf[msgWidth++] = cBuf[i];
    if (*q && msgWidth < sizeof(colBuf))
      colBuf[msgWidth++] = 0x00;   // inter-character space
  }
  if (msgWidth == 0) return;

  // Calculate left margin based on alignment
  uint16_t margin = 0;
  switch (cfg.alignText) {
    case ALIGN_CENTER: margin = (totalCols - msgWidth) / 2;       break;
    case ALIGN_RIGHT:  margin =  totalCols - msgWidth;            break;
    case ALIGN_LEFT:
    default:           margin = 0;                                break;
  }

  // Write reversed (colBuf[0] → highest physical column) to correct for
  // FC16 right-to-left column addressing, offset by margin.
  for (uint16_t i = 0; i < msgWidth && (margin + i) < totalCols; i++)
    mx->setColumn(totalCols - 1 - margin - i, colBuf[i]);
}

// ─── Animation / scroll driver ───────────────────────────────────────────────
//
// SHORT message (rendered width ≤ display width):
//   - Written statically via displayStatic() — no scrolling.
//   - Held visible for pauseMs, then cleared, then redrawn.
//   - If pauseMs == 0 the message stays on screen indefinitely until changed.
//
// LONG message (rendered width > display width):
//   - Scrolled via mx->transform() + scrollDataSource callback as before.
//   - At end-of-pass the display is blank; pause held for pauseMs then released.

bool     pausing     = false;
uint32_t pauseStart  = 0;
bool     staticShown = false;
bool     bounceRight = false;
bool     blinkOn     = false;
uint8_t  wipeCol     = 0;

void scrollText() {
  static uint32_t  prevTime    = 0;

  // ── Display-off guard ────────────────────────────────────────────────────
  if (!matrix_state) {
    mx->clear();
    return;
  }

  uint8_t  totalCols  = cfg.numDevices * COL_SIZE;
  uint16_t msgWidth   = messagePixelWidth(curMessage);
  bool     curFits    = (msgWidth > 0 && msgWidth <= totalCols);

  // ════════════════════════════════════════════════════════════════════════
  // SHORT MESSAGE — static display with visible pause then clear.
  // ════════════════════════════════════════════════════════════════════════
  if (curFits) {

    if (!staticShown) {
      displayStatic(curMessage);
      staticShown = true;
      if (cfg.pauseMs > 0) {
        pausing    = true;
        pauseStart = millis();
      }
      return;
    }

    if (pausing) {
      if (millis() - pauseStart >= cfg.pauseMs) {
        // Pause complete — load queued message now if one is waiting,
        // so the new message benefits from a fresh display cycle rather
        // than appearing immediately without the pause having run.
        if (newMessageAvailable) {
          strcpy(curMessage, newMessage);
          newMessageAvailable = false;
          passComplete = false;
          uint16_t newWidth = messagePixelWidth(curMessage);
          if (newWidth > totalCols) scrollReset = true;
        }
        mx->clear();
        pausing     = false;
        staticShown = false;
        prevTime    = millis();
      }
      return;
    }

    // pauseMs == 0: message stays visible until changed.
    // Load new message immediately — no pause to respect.
    if (newMessageAvailable) {
      strcpy(curMessage, newMessage);
      newMessageAvailable = false;
      staticShown  = false;
      passComplete = false;
      mx->clear();
      uint16_t newWidth = messagePixelWidth(curMessage);
      if (newWidth > totalCols) scrollReset = true;
    }

    return;
  }

  // ════════════════════════════════════════════════════════════════════════
  // LONG MESSAGE — scroll via transform + callback.
  // New messages are consumed exclusively inside S_PASS_END in
  // scrollDataSource — scrollText() never touches newMessage here.
  // ════════════════════════════════════════════════════════════════════════

  // ── Pause phase (display blank after pass) ────────────────────────────────
  if (pausing) {
    if (millis() - pauseStart >= cfg.pauseMs) {
      pausing      = false;
      passComplete = false;   // release S_PASS_END to start next pass
      prevTime     = millis();
    }
    return;
  }

  // ── End-of-pass: start pause or release immediately ───────────────────────
  if (passComplete) {
    if (cfg.pauseMs > 0) {
      pausing    = true;
      pauseStart = millis();
    } else {
      passComplete = false;
    }
    return;
  }

  // ── Tick gate ─────────────────────────────────────────────────────────────
  if (millis() - prevTime < cfg.scrollDelay) return;
  prevTime = millis();

  // ── Apply animation ───────────────────────────────────────────────────────
  switch (cfg.animMode) {

    case ANIM_SCROLL_LEFT:
      mx->transform(MD_MAX72XX::TSL);
      break;

    case ANIM_SCROLL_RIGHT:
      mx->transform(MD_MAX72XX::TSR);
      break;

    case ANIM_SCROLL_UP:
      mx->transform(MD_MAX72XX::TSU);
      break;

    case ANIM_SCROLL_DOWN:
      mx->transform(MD_MAX72XX::TSD);
      break;

    case ANIM_FLIP_LR:
      mx->transform(MD_MAX72XX::TFLR);
      break;

    case ANIM_FLIP_UD:
      mx->transform(MD_MAX72XX::TFUD);
      break;

    case ANIM_ROTATE_CW:
      mx->transform(MD_MAX72XX::TRC);
      break;

    case ANIM_INVERT:
      mx->transform(MD_MAX72XX::TINV);
      break;

    case ANIM_SCROLL_LEFT_INV:
      mx->transform(MD_MAX72XX::TSL);
      mx->transform(MD_MAX72XX::TINV);
      break;

    case ANIM_BOUNCE:
      if (bounceRight) {
        mx->transform(MD_MAX72XX::TSR);
        if (passComplete) { passComplete = false; bounceRight = false; }
      } else {
        mx->transform(MD_MAX72XX::TSL);
        if (passComplete) { passComplete = false; bounceRight = true; }
      }
      break;

    case ANIM_BLINK:
      if (blinkOn) {
        mx->clear();
      } else {
        newMessageAvailable = true;
        strcpy(newMessage, curMessage);
      }
      blinkOn = !blinkOn;
      break;

    case ANIM_WIPE_IN: {
      if (wipeCol >= totalCols / 2) { wipeCol = 0; }
      mx->setColumn(wipeCol,                 0xFF);
      mx->setColumn(totalCols - 1 - wipeCol, 0xFF);
      wipeCol++;
      break;
    }

    case ANIM_WIPE_OUT: {
      uint8_t centre = totalCols / 2;
      if (wipeCol >= centre) { wipeCol = 0; }
      mx->setColumn(centre - 1 - wipeCol, 0x00);
      mx->setColumn(centre + wipeCol,     0x00);
      wipeCol++;
      break;
    }

    default:
      mx->transform(MD_MAX72XX::TSL);
      break;
  }
}

// ─── UTF-8 → Latin-1 transcoder ──────────────────────────────────────────────
// MQTT payloads arrive as UTF-8. The Swedish characters we support are all in
// the Latin-1 Supplement block (U+00C0–U+00FF), encoded as two-byte sequences:
//   0xC3 0x80–0xBF  →  0xC0–0xFF
// We scan the buffer in-place and collapse each recognised two-byte pair into
// its single Latin-1 byte, shifting the remainder of the string left.
// Any unrecognised multi-byte sequence is passed through unchanged (best effort).
void utf8ToLatin1(char* buf) {
  uint8_t* r = (uint8_t*)buf;   // read pointer
  uint8_t* w = (uint8_t*)buf;   // write pointer
  while (*r) {
    if (r[0] == 0xC3 && r[1] >= 0x80 && r[1] <= 0xBF) {
      // Two-byte Latin-1 Supplement sequence
      *w++ = r[1] | 0x40;   // 0xC3 0xA5 → 0xE5, 0xC3 0x84 → 0xC4, etc.
      r += 2;
    } else if (r[0] == 0xC2 && r[1] >= 0x80 && r[1] <= 0xBF) {
      // C2 80–BF covers U+0080–U+00BF — pass through as-is
      *w++ = r[1];
      r += 2;
    } else if (r[0] >= 0x80) {
      // Other multi-byte sequence we don't handle — skip lead byte
      r++;
    } else {
      *w++ = *r++;
    }
  }
  *w = '\0';
}

// ─── MQTT helpers ────────────────────────────────────────────────────────────

// Safely extract a null-terminated string from a MQTT payload byte array.
static void payloadToStr(byte* payload, unsigned int length, char* out, unsigned int maxLen) {
  unsigned int n = (length < maxLen - 1u) ? length : maxLen - 1u;
  memcpy(out, payload, n);
  out[n] = '\0';
}

void MQTTcallback(char* topic, byte* payload, unsigned int length) {
  char val[32];
  payloadToStr(payload, length, val, sizeof(val));

  Serial.printf("MQTT [%s] → %s\n", topic, val);

  // ── Runtime topics ────────────────────────────────────────────────────────
  if (strcmp(topic, t.sub_value) == 0) {
    payloadToStr(payload, length, newMessage, MESG_SIZE);
    utf8ToLatin1(newMessage);   // collapse UTF-8 Swedish chars to Latin-1 codepoints
    newMessageAvailable = true;
    matrix_state        = true;
    clearTimer          = millis();
    return;
  }

  if (strcmp(topic, t.sub_speed) == 0) {
    uint16_t v = (uint16_t)atoi(val);
    cfg.scrollDelay = (v > 500) ? 500 : (v < 10 ? 10 : v);
    eepromSave();
    return;
  }

  if (strcmp(topic, t.sub_intensity) == 0) {
    uint8_t v = (uint8_t)atoi(val);
    cfg.intensity   = (v > 15) ? 15 : v;
    intensity_update = true;
    eepromSave();
    return;
  }

  if (strcmp(topic, t.sub_OnOff) == 0) {
    bool newState = (val[0] != '0');
    if (newState && !matrix_state) {
      // Turning back on — force a clean redisplay of the current message
      staticShown  = false;
      pausing      = false;
      passComplete = false;
      scrollReset  = true;   // resets scrollDataSource to S_IDLE cleanly
      mx->clear();
    }
    matrix_state = newState;
    return;
  }

  if (strcmp(topic, t.sub_anim) == 0) {
    uint8_t v = (uint8_t)atoi(val);
    if (v <= ANIM_MODE_MAX) {
      cfg.animMode = v;
      eepromSave();
    } else {
      client.publish(t.pub_cfg_status,
        "ERR: anim 0=scrollL 1=scrollR 2=scrollU 3=scrollD "
        "4=flipLR 5=flipUD 6=rotateCW 7=invert 8=bounce "
        "9=scrollLInv 10=blink 11=wipeIn 12=wipeOut");
    }
    return;
  }

  if (strcmp(topic, t.sub_pause) == 0) {
    // 0 = no pause; max 30000 ms (30 s)
    uint16_t v = (uint16_t)atoi(val);
    cfg.pauseMs = (v > 30000) ? 30000 : v;
    eepromSave();
    return;
  }

  if (strcmp(topic, t.sub_align) == 0) {
    uint8_t v = (uint8_t)atoi(val);
    if (v <= ALIGN_MAX) {
      cfg.alignText = v;
      eepromSave();
      // Redisplay immediately if a short message is already on screen
      if (messagePixelWidth(curMessage) <= (cfg.numDevices * COL_SIZE))
        displayStatic(curMessage);
    } else {
      client.publish(t.pub_cfg_status, "ERR: align must be 0=left 1=center 2=right");
    }
    return;
  }

  // ── Hardware-config topics ────────────────────────────────────────────────
  bool cfgChanged = false;

  if (strcmp(topic, t.sub_cfg_hwtype) == 0) {
    // 0=PAROLA_HW 1=GENERIC_HW 2=ICSTATION_HW 3=FC16_HW
    uint8_t v = (uint8_t)atoi(val);
    if (v <= 3) { cfg.hwType = v; cfgChanged = true; }
    else { client.publish(t.pub_cfg_status, "ERR: hwtype must be 0-3"); return; }
  }

  else if (strcmp(topic, t.sub_cfg_numdev) == 0) {
    uint8_t v = (uint8_t)atoi(val);
    if (v >= 1 && v <= 8) { cfg.numDevices = v; cfgChanged = true; }
    else { client.publish(t.pub_cfg_status, "ERR: numdevices must be 1-8"); return; }
  }

  else if (strcmp(topic, t.sub_cfg_clkpin) == 0) {
    uint8_t v = (uint8_t)atoi(val);
    cfg.clkPin = v; cfgChanged = true;
    // Note: CLK/DATA only affect software-SPI builds; the HW SPI constructor
    // ignores them, but we store them so the value is round-trippable.
  }

  else if (strcmp(topic, t.sub_cfg_datapin) == 0) {
    uint8_t v = (uint8_t)atoi(val);
    cfg.dataPin = v; cfgChanged = true;
  }

  else if (strcmp(topic, t.sub_cfg_cspin) == 0) {
    uint8_t v = (uint8_t)atoi(val);
    cfg.csPin = v; cfgChanged = true;
  }

  else if (strcmp(topic, t.sub_cfg_auxpin) == 0) {
    cfg.auxPin = (uint8_t)atoi(val); cfgChanged = true;
  }

  else if (strcmp(topic, t.sub_cfg_hbledpin) == 0) {
    cfg.hbLedPin = (uint8_t)atoi(val); cfgChanged = true;
  }

  else if (strcmp(topic, t.sub_cfg_hostname) == 0) {
    // Validate: non-empty, max 31 chars
    uint8_t len = strlen(val);
    if (len == 0 || len > 31) {
      client.publish(t.pub_cfg_status, "ERR: hostname must be 1-31 chars");
      return;
    }
    strncpy(cfg.hostname, val, sizeof(cfg.hostname) - 1);
    cfg.hostname[sizeof(cfg.hostname) - 1] = '\0';
    cfgChanged = true;
    // Topics will be rebuilt and MQTT will reconnect with new client ID +
    // subscriptions via the hostname_reconfigure flag below.
  }

  if (cfgChanged) {
    eepromSave();
    hardware_reconfigure = true;   // handled safely in loop()
    client.publish(t.pub_cfg_status, "OK: config saved, reinitialising...");
  }
}

// ─── MQTT reconnect ──────────────────────────────────────────────────────────
// ─── Home Assistant MQTT Discovery ───────────────────────────────────────────
// Publishes retained discovery payloads so HA auto-creates entities.
// Called once per MQTT connect from reconnect().
// Each payload is built into a stack buffer and published with retain=true.
void publishHADiscovery() {
  const char* h  = cfg.hostname;
  char buf[700];

  // ── Device block (shared by all entities) ───────────────────────────────
  // Inlined into each payload since PubSubClient has no multi-topic atomicity.
  char devBlock[160];
  snprintf(devBlock, sizeof(devBlock),
    "\"dev\":{\"ids\":\"%s\",\"name\":\"%s\",\"mdl\":\"LED Matrix\","
    "\"mf\":\"MAX7219\",\"sw\":\"1.0\"}",
    h, h);

  // ── 1. Text entity — message display ────────────────────────────────────
  snprintf(buf, sizeof(buf),
    "{\"name\":\"Message\","
    "\"uniq_id\":\"%s_message\","
    "\"cmd_t\":\"%s\","
    "\"max\":254,"
    "\"avty_t\":\"%s\","
    "%s}",
    h, t.sub_value, t.pup_alive, devBlock);
  client.publish(t.ha_text, buf, true);
  client.loop();

  // ── 2. Light entity — on/off + brightness (intensity 0-15) ──────────────
  snprintf(buf, sizeof(buf),
    "{\"name\":\"Display\","
    "\"uniq_id\":\"%s_display\","
    "\"cmd_t\":\"%s\","
    "\"stat_t\":\"%s\","
    "\"bri_cmd_t\":\"%s\","
    "\"bri_stat_t\":\"%s\","
    "\"bri_scl\":15,"
    "\"on_cmd_type\":\"brightness\","
    "\"payload_on\":\"1\","
    "\"payload_off\":\"0\","
    "\"avty_t\":\"%s\","
    "%s}",
    h,
    t.sub_OnOff, t.sub_OnOff,
    t.sub_intensity, t.sub_intensity,
    t.pup_alive, devBlock);
  client.publish(t.ha_light, buf, true);
  client.loop();

  // ── 3. Number entity — scroll speed ─────────────────────────────────────
  snprintf(buf, sizeof(buf),
    "{\"name\":\"Scroll Speed\","
    "\"uniq_id\":\"%s_speed\","
    "\"cmd_t\":\"%s\","
    "\"min\":10,\"max\":500,\"step\":10,"
    "\"unit_of_meas\":\"ms\","
    "\"icon\":\"mdi:speedometer\","
    "\"avty_t\":\"%s\","
    "%s}",
    h, t.sub_speed, t.pup_alive, devBlock);
  client.publish(t.ha_speed, buf, true);
  client.loop();

  // ── 4. Number entity — pause between passes ──────────────────────────────
  snprintf(buf, sizeof(buf),
    "{\"name\":\"Pause\","
    "\"uniq_id\":\"%s_pause\","
    "\"cmd_t\":\"%s\","
    "\"min\":0,\"max\":30000,\"step\":500,"
    "\"unit_of_meas\":\"ms\","
    "\"icon\":\"mdi:timer-pause\","
    "\"avty_t\":\"%s\","
    "%s}",
    h, t.sub_pause, t.pup_alive, devBlock);
  client.publish(t.ha_pause, buf, true);
  client.loop();

  // ── 5. Select entity — animation mode ───────────────────────────────────
  snprintf(buf, sizeof(buf),
    "{\"name\":\"Animation\","
    "\"uniq_id\":\"%s_anim\","
    "\"cmd_t\":\"%s\","
    "\"options\":[\"0\",\"1\",\"2\",\"3\",\"4\",\"5\","
                 "\"6\",\"7\",\"8\",\"9\",\"10\",\"11\",\"12\"],"
    "\"icon\":\"mdi:animation\","
    "\"avty_t\":\"%s\","
    "%s}",
    h, t.sub_anim, t.pup_alive, devBlock);
  client.publish(t.ha_anim, buf, true);
  client.loop();

  // ── 6. Select entity — text alignment ───────────────────────────────────
  snprintf(buf, sizeof(buf),
    "{\"name\":\"Alignment\","
    "\"uniq_id\":\"%s_align\","
    "\"cmd_t\":\"%s\","
    "\"options\":[\"0\",\"1\",\"2\"],"
    "\"icon\":\"mdi:format-align-left\","
    "\"avty_t\":\"%s\","
    "%s}",
    h, t.sub_align, t.pup_alive, devBlock);
  client.publish(t.ha_align, buf, true);
  client.loop();

  Serial.println(F("HA discovery published."));
}

void reconnect() {
  while (!client.connected()) {
    Serial.println(F("Attempting MQTT connection..."));
    if (client.connect(cfg.hostname)) {
      client.publish(t.pup_alive, "online");

      // Runtime subscriptions
      client.subscribe(t.sub_value);      client.loop();
      client.subscribe(t.sub_speed);      client.loop();
      client.subscribe(t.sub_intensity);  client.loop();
      client.subscribe(t.sub_OnOff);      client.loop();
      client.subscribe(t.sub_anim);       client.loop();
      client.subscribe(t.sub_pause);      client.loop();
      client.subscribe(t.sub_align);      client.loop();

      // Hardware-config subscriptions
      client.subscribe(t.sub_cfg_hwtype);   client.loop();
      client.subscribe(t.sub_cfg_numdev);   client.loop();
      client.subscribe(t.sub_cfg_clkpin);   client.loop();
      client.subscribe(t.sub_cfg_datapin);  client.loop();
      client.subscribe(t.sub_cfg_cspin);    client.loop();
      client.subscribe(t.sub_cfg_auxpin);   client.loop();
      client.subscribe(t.sub_cfg_hbledpin); client.loop();
      client.subscribe(t.sub_cfg_hostname); client.loop();  // fixed "/matrix/config/hostname"

      Serial.println(F("MQTT connected."));

      // Publish HA discovery payloads so entities appear automatically
      publishHADiscovery();

      // On the very first connect show confirmation on the matrix.
      // Subsequent reconnects are silent so they don't interrupt a
      // message that was already scrolling.
      if (firstConnect) {
        firstConnect = false;
        strcpy(curMessage, "WiFi+MQTT connected!");
        newMessageAvailable = false;   // display curMessage directly
      }

      // Publish current config for inspection
      char info[300];
      snprintf(info, sizeof(info),
        "fw=%s hostname=%s mqtt=%s:%u hwType=%d devices=%d clk=%d data=%d cs=%d aux=%d hbled=%d speed=%d intensity=%d anim=%d pause=%u align=%d",
        FW_VERSION,
        cfg.hostname, cfg.mqttServer, cfg.mqttPort,
        cfg.hwType, cfg.numDevices, cfg.clkPin, cfg.dataPin,
        cfg.csPin, cfg.auxPin, cfg.hbLedPin, cfg.scrollDelay, cfg.intensity,
        cfg.animMode, cfg.pauseMs, cfg.alignText);
      // Publish IP address for easy OTA targeting
      char ipStr[16];
      WiFi.localIP().toString().toCharArray(ipStr, sizeof(ipStr));
      client.publish((String(cfg.hostname) + "/ip").c_str(), ipStr, true);  // retain=true
      client.publish(t.pub_cfg_status, info);

    } else {
      Serial.printf("MQTT failed rc=%d – retry in 5 s\n", client.state());
      delay(5000);
    }
  }
}

// ─── Setup ───────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);

#if LED_HEARTBEAT
  pinMode(cfg.hbLedPin, OUTPUT);
  digitalWrite(cfg.hbLedPin, LOW);
#endif

  pinMode(cfg.auxPin, OUTPUT);
  digitalWrite(cfg.auxPin, LOW);

  // Load saved config (falls back to defaults if EEPROM is blank)
  eepromLoad();

  // Build all MQTT topic strings from the loaded hostname
  buildTopics();

  // ── WiFi + MQTT broker provisioning via captive portal ─────────────────────
  // Custom parameters appear as extra fields in the WiFiManager config page.
  // Values are pre-filled from EEPROM so re-provisioning preserves them.
  char mqttPortStr[6];
  snprintf(mqttPortStr, sizeof(mqttPortStr), "%u", cfg.mqttPort);

  WiFiManagerParameter wmMqttServer(
    "mqtt_server",          // HTML id
    "MQTT Broker IP",       // label
    cfg.mqttServer,         // current value (pre-filled)
    39                      // max length
  );
  WiFiManagerParameter wmMqttPort(
    "mqtt_port",
    "MQTT Port",
    mqttPortStr,
    5
  );

  WiFiManager wm;
  wm.addParameter(&wmMqttServer);
  wm.addParameter(&wmMqttPort);

  // Block here until connected. If no saved credentials the portal opens on
  // AP "LAUFSCHRIFT" — the user sets Wi-Fi SSID/password AND broker details
  // in the same captive portal page before pressing Save.
  if (!wm.autoConnect("MQTT Matrix Setup")) {
    Serial.println(F("WiFiManager failed — rebooting."));
    delay(3000);
    ESP.restart();
  }

  Serial.print(F("WiFi connected – IP: "));
  Serial.println(WiFi.localIP());

  // Retrieve whatever the user typed (or kept) in the portal fields
  const char* newServer = wmMqttServer.getValue();
  uint16_t    newPort   = (uint16_t)atoi(wmMqttPort.getValue());

  // Persist to EEPROM if either value changed
  bool mqttCfgChanged = false;

  if (strlen(newServer) > 0 && strcmp(newServer, cfg.mqttServer) != 0) {
    strncpy(cfg.mqttServer, newServer, sizeof(cfg.mqttServer) - 1);
    cfg.mqttServer[sizeof(cfg.mqttServer) - 1] = '\0';
    mqttCfgChanged = true;
  }
  if (newPort > 0 && newPort != cfg.mqttPort) {
    cfg.mqttPort   = newPort;
    mqttCfgChanged = true;
  }
  if (mqttCfgChanged) {
    eepromSave();
    Serial.printf("MQTT broker saved: %s:%u\n", cfg.mqttServer, cfg.mqttPort);
  }

  // Guard: if broker is still blank after the portal, show an error on the
  // display and reopen the portal on the next boot.
  if (strlen(cfg.mqttServer) == 0) {
    Serial.println(F("ERROR: No MQTT broker configured — rebooting to portal."));
    // Clear saved WiFi credentials so the portal opens again automatically
    wm.resetSettings();
    delay(3000);
    ESP.restart();
  }

  OTA_setup();

  // Initialise the matrix with whatever config we loaded
  matrixInit();

  // Show a connecting message while MQTT handshake happens in loop()
  strcpy(curMessage, "connecting...");

  client.setServer(cfg.mqttServer, cfg.mqttPort);
  client.setCallback(MQTTcallback);
}

// ─── Loop ────────────────────────────────────────────────────────────────────
void loop() {
  if (!client.connected()) reconnect();
  client.loop();

  ArduinoOTA.handle();

  // Hardware/hostname reconfigure deferred from MQTT callback to loop() to
  // avoid running on the PubSubClient stack.
  if (hardware_reconfigure) {
    hardware_reconfigure = false;

    // Save whatever was on screen so we can restore it after the notification
    char savedMessage[MESG_SIZE];
    strncpy(savedMessage, curMessage, MESG_SIZE - 1);
    savedMessage[MESG_SIZE - 1] = '\0';

    buildTopics();
    client.disconnect();
    matrixInit();

    // Show "reconfigured!" once, then restore the previous message
    strcpy(curMessage, "reconfigured!");
    strcpy(newMessage, savedMessage);
    newMessageAvailable = true;

    // Reset scroll state so "reconfigured!" starts cleanly
    scrollReset  = false;
    passComplete = false;
    staticShown  = false;
    pausing      = false;
  }

  if (intensity_update) {
    intensity_update = false;
    mx->control(MD_MAX72XX::INTENSITY, cfg.intensity);
  }

#if LED_HEARTBEAT
  static uint32_t timeLast = 0;
  if (millis() - timeLast >= HB_LED_TIME) {
    digitalWrite(cfg.hbLedPin, digitalRead(cfg.hbLedPin) == LOW ? HIGH : LOW);
    timeLast = millis();
  }
#endif

  scrollText();
}
