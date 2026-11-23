/* Original portable telemetry encoder. See repository LICENSE. */
#ifndef RF_TELEMETRY_H
#define RF_TELEMETRY_H

#include <stddef.h>
#include <stdint.h>

#define RF_TELEMETRY_FRAME_SIZE 17u

/* Encode the exact Python protocol. Returns 17, or 0 for bad input/capacity.
 * out must point to capacity writable bytes; button must be 0 or 1.
 * The function performs no I/O and depends on no STM32 HAL component.
 */
size_t rf_telemetry_encode(uint8_t *out, size_t capacity,
                           uint32_t sequence, uint32_t uptime_ms,
                           uint8_t button);

#endif
