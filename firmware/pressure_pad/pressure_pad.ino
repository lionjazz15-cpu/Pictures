// ChairMotion pressure pad v0 firmware (Raspberry Pi Pico / RP2040)
//
// Board core: "Raspberry Pi Pico/RP2040" by Earle F. Philhower (arduino-pico).
// Set PAD_KIND below before flashing: one Pico per pad.
//   PAD_CHAIR: 28 rows x 16 cols (seat 16 rows + back 12 rows, columns shared)
//   PAD_FLOOR: 16 rows x 16 cols
//   PAD_TEST : 4 rows x 4 cols (breadboard practice circuit, docs/08)
//
// Scans a piezoresistive matrix with the zero-potential method:
//   - every column is held at Vb by a transimpedance amplifier (TIA)
//   - unselected rows are pulled up to Vb  -> no current through their cells
//   - the selected row is pulled to GND by a low-side driver (TBD62083A)
// TIA output = Vb + I_cell * Rf, so (raw - vb_raw) is proportional to the
// conductance of the single selected cell.
//
// Wire protocol (USB CDC), see tools/pressure_pad/protocol.py:
//   binary frame : "CMSP" + header + uint16 raw[ROWS*COLS] + crc16 (little endian)
//   text replies : lines starting with '#'
//   commands     : START, STOP, INFO, PING <host_ns>, RATE <hz>
//   debug (usable from the Arduino IDE serial monitor):
//                  DUMP        print one scan as text (ADC counts above Vb)
//                  HOLD <row>  stop streaming and keep <row> selected (-1 = none),
//                              so row voltages can be checked with a multimeter

#include <Arduino.h>

#define PAD_CHAIR 0
#define PAD_FLOOR 1
#define PAD_TEST 2
#define PAD_KIND PAD_CHAIR  // <- change to PAD_FLOOR / PAD_TEST

// ---- pin map --------------------------------------------------------------
static const uint8_t PIN_SRCLK = 2;   // 74HC595 SRCLK (shift clock)
static const uint8_t PIN_SER = 3;     // 74HC595 SER (data)
static const uint8_t PIN_RCLK = 7;    // 74HC595 RCLK (latch)
static const uint8_t PIN_MUX_S[4] = {10, 11, 12, 13};  // CD74HC4067 S0..S3
static const uint8_t PIN_ADC_SIG = 26;  // ADC0: mux SIG (TIA outputs)
static const uint8_t PIN_ADC_VB = 27;   // ADC1: Vb sense
static const uint8_t PIN_SMPS_PWM = 23; // HIGH = PWM mode, lower ADC noise

// ---- matrix geometry ------------------------------------------------------
#if PAD_KIND == PAD_CHAIR
static const uint8_t MAIN_ROWS = 16;    // seat
static const uint8_t EXTRA_ROWS = 12;   // back
static const uint8_t DRIVER_BITS = 32;  // 4 x 74HC595
static const uint8_t COLS = 16;
#elif PAD_KIND == PAD_FLOOR
static const uint8_t MAIN_ROWS = 16;    // floor
static const uint8_t EXTRA_ROWS = 0;
static const uint8_t DRIVER_BITS = 16;  // 2 x 74HC595
static const uint8_t COLS = 16;
#elif PAD_KIND == PAD_TEST
static const uint8_t MAIN_ROWS = 4;     // practice mat
static const uint8_t EXTRA_ROWS = 0;
static const uint8_t DRIVER_BITS = 8;   // 1 x 74HC595
static const uint8_t COLS = 4;          // mux channels C0..C3
#else
#error "unknown PAD_KIND"
#endif
static const uint8_t ROWS = MAIN_ROWS + EXTRA_ROWS;
// Shift-register output index for each logical row.
// Main rows 0..15 -> U1/U2 Q0..Q7, back rows 0..11 -> U3 Q0..Q7, U4 Q0..Q3.
static uint8_t rowToBit(uint8_t row) { return row; }

// ---- timing ---------------------------------------------------------------
static const uint16_t ROW_SETTLE_US = 30;
static const uint16_t MUX_SETTLE_US = 4;
static const uint8_t ADC_AVG = 2;

// ---- protocol -------------------------------------------------------------
static const uint8_t PROTO_VERSION = 1;
static const uint8_t FLAG_SATURATED = 0x01;
static const size_t HEADER_LEN = 4 + 1 + 1 + 1 + 1 + 4 + 8 + 2 + 2;
static const size_t FRAME_LEN = HEADER_LEN + ROWS * COLS * 2 + 2;

static uint8_t frameBuf[FRAME_LEN];
static bool streaming = false;
static uint32_t framePeriodUs = 1000000UL / 60;
static uint32_t seq = 0;

// micros() wraps every ~71 minutes; extend it to 64 bits.
static uint64_t micros64() {
  static uint32_t last = 0;
  static uint64_t high = 0;
  uint32_t now = micros();
  if (now < last) high += (1ULL << 32);
  last = now;
  return high | now;
}

static uint16_t crc16Ccitt(const uint8_t *data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= (uint16_t)data[i] << 8;
    for (uint8_t b = 0; b < 8; b++) {
      crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : (crc << 1);
    }
  }
  return crc;
}

// printf's %llu support depends on the libc build; format 64-bit values by hand.
static const char *u64ToStr(uint64_t v) {
  static char buf[21];
  char *p = buf + sizeof(buf) - 1;
  *p = 0;
  do { *--p = '0' + (v % 10); v /= 10; } while (v);
  return p;
}

static void put16(uint8_t *p, uint16_t v) { p[0] = v; p[1] = v >> 8; }
static void put32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = v >> (8 * i); }
static void put64(uint8_t *p, uint64_t v) { for (int i = 0; i < 8; i++) p[i] = v >> (8 * i); }

// ---- hardware access ------------------------------------------------------
// Shifts 32 bits so that bit k ends up on output k of the chain
// (k = 0..7 on U1 nearest SER, 8..15 on U2, ...). HIGH = row selected.
static void selectDriverBit(int8_t bit) {
  for (int8_t k = DRIVER_BITS - 1; k >= 0; k--) {
    digitalWrite(PIN_SER, k == bit ? HIGH : LOW);
    digitalWrite(PIN_SRCLK, HIGH);
    digitalWrite(PIN_SRCLK, LOW);
  }
  digitalWrite(PIN_RCLK, HIGH);
  digitalWrite(PIN_RCLK, LOW);
}

static void selectColumn(uint8_t col) {
  for (uint8_t i = 0; i < 4; i++) digitalWrite(PIN_MUX_S[i], (col >> i) & 1);
}

static uint16_t readAdc(uint8_t pin) {
  uint32_t sum = 0;
  for (uint8_t i = 0; i < ADC_AVG; i++) sum += analogRead(pin);
  return sum / ADC_AVG;
}

// ---- scanning -------------------------------------------------------------
// Fills the data part of frameBuf; returns flags.
static uint8_t scanInto(uint16_t &vb) {
  uint8_t flags = 0;
  uint8_t *data = frameBuf + HEADER_LEN;
  vb = readAdc(PIN_ADC_VB);
  for (uint8_t r = 0; r < ROWS; r++) {
    selectDriverBit(rowToBit(r));
    delayMicroseconds(ROW_SETTLE_US);
    for (uint8_t c = 0; c < COLS; c++) {
      selectColumn(c);
      delayMicroseconds(MUX_SETTLE_US);
      uint16_t v = readAdc(PIN_ADC_SIG);
      if (v >= 4090) flags |= FLAG_SATURATED;
      put16(data + 2 * (r * COLS + c), v);
    }
  }
  selectDriverBit(-1);  // release all rows between frames
  return flags;
}

static uint16_t cellAt(uint8_t r, uint8_t c) {
  const uint8_t *p = frameBuf + HEADER_LEN + 2 * (r * COLS + c);
  return p[0] | (p[1] << 8);
}

static void dumpFrame() {
  uint16_t vb;
  uint8_t flags = scanInto(vb);
  Serial.printf("#DUMP vb=%u (about 2048 is normal)%s\n", vb, flags & FLAG_SATURATED ? " SATURATED" : "");
  for (uint8_t r = 0; r < ROWS; r++) {
    String line = "#row" + String(r) + (r < 10 ? " " : "") + ":";
    for (uint8_t c = 0; c < COLS; c++) {
      int v = (int)cellAt(r, c) - (int)vb;
      String cell = String(v < 0 ? 0 : v);
      while (cell.length() < 5) cell = " " + cell;
      line += cell;
    }
    Serial.println(line);
  }
}

static void scanFrame() {
  uint64_t t0 = micros64();
  uint16_t vb;
  uint8_t flags = scanInto(vb);

  uint8_t *h = frameBuf;
  memcpy(h, "CMSP", 4);
  h[4] = PROTO_VERSION;
  h[5] = flags;
  h[6] = ROWS;
  h[7] = COLS;
  put32(h + 8, seq++);
  put64(h + 12, t0);
  put16(h + 20, vb);
  // low byte: rows [0, MAIN_ROWS) are seat/floor, the rest back; high byte: pad kind
  put16(h + 22, MAIN_ROWS | (PAD_KIND << 8));
  uint16_t crc = crc16Ccitt(frameBuf + 4, FRAME_LEN - 4 - 2);
  put16(frameBuf + FRAME_LEN - 2, crc);
  Serial.write(frameBuf, FRAME_LEN);
}

// ---- commands -------------------------------------------------------------
static void handleCommand(const String &line) {
  if (line == "START") {
    streaming = true;
  } else if (line == "STOP") {
    streaming = false;
  } else if (line == "INFO") {
    static const char *kindNames[] = {"chair", "floor", "test"};
    Serial.printf("#INFO {\"fw\":\"pressure_pad_v0\",\"kind\":\"%s\",\"proto\":%u,\"rows\":%u,"
                  "\"main_rows\":%u,\"cols\":%u,\"rate_hz\":%lu}\n",
                  kindNames[PAD_KIND], PROTO_VERSION, ROWS, MAIN_ROWS, COLS, 1000000UL / framePeriodUs);
  } else if (line == "DUMP") {
    streaming = false;
    dumpFrame();
  } else if (line.startsWith("HOLD ")) {
    streaming = false;
    long row = line.substring(5).toInt();
    if (row >= 0 && row < ROWS) {
      selectDriverBit(rowToBit(row));
      Serial.printf("#OK row %ld is now at 0 V, the other rows should be at Vb (about 1.65 V)\n", row);
    } else {
      selectDriverBit(-1);
      Serial.println("#OK no row selected, all rows should be at Vb (about 1.65 V)");
    }
  } else if (line.startsWith("PING ")) {
    // Echo the host timestamp with our clock, for offset/drift estimation.
    Serial.printf("#PONG %s %s\n", line.substring(5).c_str(), u64ToStr(micros64()));
  } else if (line.startsWith("RATE ")) {
    long hz = line.substring(5).toInt();
    if (hz >= 1 && hz <= 120) framePeriodUs = 1000000UL / hz;
  } else if (line.length() > 0) {
    Serial.printf("#ERR unknown command: %s\n", line.c_str());
  }
}

static void pollSerial() {
  static String line;
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      line.trim();
      handleCommand(line);
      line = "";
    } else if (line.length() < 64) {
      line += ch;
    }
  }
}

void setup() {
  pinMode(PIN_SRCLK, OUTPUT);
  pinMode(PIN_SER, OUTPUT);
  pinMode(PIN_RCLK, OUTPUT);
  for (uint8_t i = 0; i < 4; i++) pinMode(PIN_MUX_S[i], OUTPUT);
  pinMode(PIN_SMPS_PWM, OUTPUT);
  digitalWrite(PIN_SMPS_PWM, HIGH);
  analogReadResolution(12);
  selectDriverBit(-1);
  Serial.begin(115200);  // USB CDC; the baud rate is ignored
}

void loop() {
  static uint64_t nextFrame = 0;
  pollSerial();
  if (!streaming) return;
  uint64_t now = micros64();
  if (now < nextFrame) return;
  nextFrame = (nextFrame == 0 || now - nextFrame > framePeriodUs) ? now + framePeriodUs
                                                                  : nextFrame + framePeriodUs;
  scanFrame();
}
