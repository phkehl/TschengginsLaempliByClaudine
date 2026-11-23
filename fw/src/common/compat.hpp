// ESP32 and ESP8266 differences for the common code
//
// - Mutex: std::mutex on the ESP32 (tasks). The ESP8266 has one task only (the Arduino loop, and functions that run
//   at yield() or delay()), so the lock does nothing there.
// - Atomic: std::atomic on the ESP32. The ESP8266 toolchain has no libatomic (__atomic_fetch_add_4 etc.); one task
//   only, so a plain value.
// - Heap info: largest free block and lowest free heap (ESP8266: no counter, the lowest value of heapSample() at the
//   points of high use: message, LED state, TLS connect, report), heap size (ESP8266: from the linker symbol)
// - CPU name: chip model, revision, cores, clock
// - Firmware update: possible if there is a second app partition (ESP32: not on the Gitta, 2 MB flash)
// - Preferences: the ESP32 class (NVS), on the ESP8266 the same API on LittleFS (prefs8266.hpp)
// - yield(): see compat.cpp (FastLED has an empty one)

#ifndef COMPAT_HPP
#define COMPAT_HPP

#include <Arduino.h>

#include <atomic>
#include <mutex>

#if defined(ESP8266)
#include "prefs8266.hpp"

// Lock that does nothing (one task only)
struct Mutex
{
    void lock() {}
    void unlock() {}
};

// Value with the std::atomic API (subset), no atomic operations (one task only)
template <typename T>
class Atomic
{
  public:
    Atomic(const T val = T()) : value(val) {}
    T load() const { return value; }
    T exchange(const T val)
    {
        const T old = value;
        value = val;
        return old;
    }
    operator T() const { return value; }
    Atomic& operator=(const T val)
    {
        value = val;
        return *this;
    }
    T operator++(int) { return value++; }

  private:
    T value;
};

/**
 * @brief Get largest free heap block
 */
static inline uint32_t heapMaxBlock()
{
    return ESP.getMaxFreeBlockSize();
}

// Start of the heap (linker), the heap ends at 0x3FFFC000 (umm_malloc_cfgport.h)
extern char _heap_start[];

/**
 * @brief Get heap size (the heap of malloc, without the IRAM heap)
 */
static inline uint32_t heapSize()
{
    return (0x3FFFC000UL - (uint32_t)&_heap_start[0]);
}

/**
 * @brief Get CPU name, e.g. "ESP8266EX, 1 core, 80 MHz"
 *
 * @param[out] buf   Buffer
 * @param[in]  size  Size of buf
 */
static inline void cpuName(char* buf, const size_t size)
{
    snprintf(buf, size, "ESP8266EX, 1 core, %u MHz", (unsigned)ESP.getCpuFreqMHz());
}

/**
 * @brief Check if firmware update is possible
 */
static inline bool otaPossible()
{
    return true;
}

// Lowest free heap seen by heapSample()
inline uint32_t heapLowest = UINT32_MAX;

/**
 * @brief Note the free heap now (for heapMinFree())
 */
static inline void heapSample()
{
    const uint32_t now = ESP.getFreeHeap();
    if (now < heapLowest) {
        heapLowest = now;
    }
}

/**
 * @brief Get lowest free heap since start (sampled, see heapSample())
 */
static inline uint32_t heapMinFree()
{
    heapSample();
    return heapLowest;
}

#else
#include <Preferences.h>
#include <esp_ota_ops.h>

using Mutex = std::mutex;

template <typename T>
using Atomic = std::atomic<T>;

/**
 * @brief Get largest free heap block
 */
static inline uint32_t heapMaxBlock()
{
    return ESP.getMaxAllocHeap();
}

/**
 * @brief Get heap size
 */
static inline uint32_t heapSize()
{
    return ESP.getHeapSize();
}

/**
 * @brief Get CPU name, e.g. "ESP32-D0WD-V3 rev 3, 2 cores, 240 MHz"
 *
 * @param[out] buf   Buffer
 * @param[in]  size  Size of buf
 */
static inline void cpuName(char* buf, const size_t size)
{
    snprintf(buf, size, "%s rev %u, %u cores, %u MHz", ESP.getChipModel(), (unsigned)ESP.getChipRevision(),
        (unsigned)ESP.getChipCores(), (unsigned)ESP.getCpuFreqMHz());
}

/**
 * @brief Check if firmware update is possible (second app partition)
 */
static inline bool otaPossible()
{
    return (esp_ota_get_next_update_partition(nullptr) != nullptr);
}

/**
 * @brief Get lowest free heap since start
 */
static inline uint32_t heapMinFree()
{
    return ESP.getMinFreeHeap();
}

/**
 * @brief Note the free heap now (ESP32: not needed, it has a counter)
 */
static inline void heapSample() {}

#endif

using Lock = std::lock_guard<Mutex>;

#endif // COMPAT_HPP
