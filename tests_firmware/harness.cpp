// Command-line wrapper around esphome/common/footfall.h so that the node's
// logic can be driven from pytest on a desktop. Reads one command per line on
// stdin and answers each query on stdout.
//
//   observe <hex address> <rssi> <floor>
//   pending                      -> "pending <n>"
//   strongest                    -> "strongest <rssi>" or "strongest nan"
//   drain <salt> <day>           -> "drain <payload>"
//   id <hex address> <salt> <day>-> "id <8 hex digits>"
//   max                          -> "max <MAX_DEVICES>"
//   clear

#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <iostream>
#include <sstream>
#include <string>

#include "footfall.h"

int main() {
  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream in(line);
    std::string cmd;
    if (!(in >> cmd)) continue;

    if (cmd == "observe") {
      std::string addr;
      int rssi, floor;
      in >> addr >> rssi >> floor;
      footfall::observe(std::stoull(addr, nullptr, 16), rssi, floor);
    } else if (cmd == "pending") {
      std::printf("pending %zu\n", footfall::pending());
    } else if (cmd == "strongest") {
      const float s = footfall::strongest();
      if (std::isnan(s)) {
        std::printf("strongest nan\n");
      } else {
        std::printf("strongest %d\n", (int) s);
      }
    } else if (cmd == "drain") {
      std::string salt;
      uint32_t day;
      in >> salt >> day;
      std::printf("drain %s\n", footfall::drain(salt.c_str(), day).c_str());
    } else if (cmd == "id") {
      std::string addr, salt;
      uint32_t day;
      in >> addr >> salt >> day;
      std::printf("id %08" PRIx32 "\n", footfall::anon_id(std::stoull(addr, nullptr, 16), salt.c_str(), day));
    } else if (cmd == "max") {
      std::printf("max %zu\n", footfall::MAX_DEVICES);
    } else if (cmd == "clear") {
      footfall::clear();
    } else {
      std::fprintf(stderr, "unknown command: %s\n", cmd.c_str());
      return 2;
    }
  }
  return 0;
}
