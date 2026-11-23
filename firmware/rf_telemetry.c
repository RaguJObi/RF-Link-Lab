/* Original portable telemetry encoder. See repository LICENSE. */
#include "rf_telemetry.h"

static void put_u32_le(uint8_t *out, uint32_t value)
{
    out[0] = (uint8_t)(value & UINT32_C(0xff));
    out[1] = (uint8_t)((value >> 8) & UINT32_C(0xff));
    out[2] = (uint8_t)((value >> 16) & UINT32_C(0xff));
    out[3] = (uint8_t)((value >> 24) & UINT32_C(0xff));
}

/* Reflected IEEE CRC32 compatible with Python zlib.crc32. */
static uint32_t crc32(const uint8_t *data, size_t length)
{
    uint32_t crc = UINT32_C(0xffffffff);
    size_t index;
    unsigned int bit;
    for (index = 0; index < length; ++index) {
        crc ^= (uint32_t)data[index];
        for (bit = 0; bit < 8u; ++bit) {
            if ((crc & UINT32_C(1)) != 0u) {
                crc = (crc >> 1) ^ UINT32_C(0xedb88320);
            } else {
                crc >>= 1;
            }
        }
    }
    return crc ^ UINT32_C(0xffffffff);
}

size_t rf_telemetry_encode(uint8_t *out, size_t capacity,
                           uint32_t sequence, uint32_t uptime_ms,
                           uint8_t button)
{
    if (out == NULL || capacity < RF_TELEMETRY_FRAME_SIZE || button > 1u) {
        return 0u;
    }
    out[0] = UINT8_C(0xb5);
    out[1] = UINT8_C(0x62);
    out[2] = UINT8_C(1);
    out[3] = UINT8_C(9);
    put_u32_le(&out[4], sequence);
    put_u32_le(&out[8], uptime_ms);
    out[12] = button;
    put_u32_le(&out[13], crc32(out, 13u));
    return RF_TELEMETRY_FRAME_SIZE;
}
