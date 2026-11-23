/* Host-side golden-vector check. No board or HAL is required. */
#include "rf_telemetry.h"
#include <stdio.h>
#include <string.h>

int main(void)
{
    const uint8_t expected[RF_TELEMETRY_FRAME_SIZE] = {
        0xb5, 0x62, 0x01, 0x09, 0x01, 0x00, 0x00, 0x00,
        0xe8, 0x03, 0x00, 0x00, 0x01, 0x11, 0x3c, 0xd7, 0x26
    };
    uint8_t out[RF_TELEMETRY_FRAME_SIZE];
    if (rf_telemetry_encode(out, sizeof(out), UINT32_C(1), UINT32_C(1000), 1u) != RF_TELEMETRY_FRAME_SIZE ||
        memcmp(out, expected, sizeof(out)) != 0) {
        fputs("Golden telemetry vector failed\n", stderr);
        return 1;
    }
    if (rf_telemetry_encode(NULL, sizeof(out), 0u, 0u, 0u) != 0u ||
        rf_telemetry_encode(out, sizeof(out) - 1u, 0u, 0u, 0u) != 0u ||
        rf_telemetry_encode(out, sizeof(out), 0u, 0u, 2u) != 0u) {
        fputs("Invalid input check failed\n", stderr);
        return 1;
    }
    puts("Telemetry codec golden vector and invalid-input checks passed");
    return 0;
}
