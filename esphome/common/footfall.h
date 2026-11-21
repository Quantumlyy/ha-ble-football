// Per-interval BLE sighting buffer for the footfall nodes.
//
// observe() is called for every advertisement and keeps the strongest RSSI per
// device since the last report. drain() turns the buffer into a compact string
// of anonymised ids ("1a2b3c4d:-61;9f8e7d6c:-74;") and empties it.
//
// Raw MAC addresses never leave this file: only a salted hash is reported, and
// the hash changes every UTC day so ids cannot be linked across days.
#pragma once

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace footfall {

struct Seen {
  uint64_t addr;
  int8_t rssi;
};

// Upper bound on devices per report. Keeps RAM and the event payload bounded;
// when full, the weakest device is replaced by a stronger newcomer.
static const size_t MAX_DEVICES = 48;

inline std::vector<Seen> &buffer() {
  static std::vector<Seen> buf;
  return buf;
}

inline void observe(uint64_t addr, int rssi, int floor) {
  if (rssi < floor) return;
  if (rssi > 0) rssi = 0;
  if (rssi < -127) rssi = -127;
  auto &buf = buffer();
  Seen *weakest = nullptr;
  for (auto &s : buf) {
    if (s.addr == addr) {
      if (rssi > s.rssi) s.rssi = (int8_t) rssi;
      return;
    }
    if (weakest == nullptr || s.rssi < weakest->rssi) weakest = &s;
  }
  if (buf.size() < MAX_DEVICES) {
    buf.push_back({addr, (int8_t) rssi});
  } else if (weakest != nullptr && rssi > weakest->rssi) {
    *weakest = {addr, (int8_t) rssi};
  }
}

inline size_t pending() { return buffer().size(); }

// Strongest RSSI heard since the last report, or NAN if nothing was heard.
// Used for calibration: hold one phone 1 m from the node and read this.
inline float strongest() {
  const auto &buf = buffer();
  if (buf.empty()) return NAN;
  int best = -127;
  for (const auto &s : buf) {
    if (s.rssi > best) best = s.rssi;
  }
  return (float) best;
}

inline void clear() { buffer().clear(); }

// 32-bit FNV-1a over salt, day number and the 6 address bytes.
inline uint32_t anon_id(uint64_t addr, const char *salt, uint32_t day) {
  uint32_t h = 2166136261u;
  auto mix = [&h](uint8_t b) {
    h ^= b;
    h *= 16777619u;
  };
  for (const char *p = salt; *p != '\0'; p++) mix((uint8_t) *p);
  for (int i = 0; i < 4; i++) mix((uint8_t) (day >> (8 * i)));
  for (int i = 0; i < 6; i++) mix((uint8_t) (addr >> (8 * i)));
  return h;
}

inline std::string drain(const char *salt, uint32_t day) {
  auto &buf = buffer();
  std::string out;
  out.reserve(buf.size() * 14);
  char item[20];
  for (const auto &s : buf) {
    snprintf(item, sizeof(item), "%08x:%d;", (unsigned int) anon_id(s.addr, salt, day), (int) s.rssi);
    out += item;
  }
  buf.clear();
  return out;
}

}  // namespace footfall
