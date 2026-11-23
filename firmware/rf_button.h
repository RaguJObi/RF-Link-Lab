/* Deterministic button debouncer, independent of STM32 registers. */
#ifndef RF_BUTTON_H
#define RF_BUTTON_H
#include <stdint.h>
typedef struct {
    uint32_t candidate_since_ms;
    uint8_t stable;
    uint8_t candidate;
} rf_button_state;
void rf_button_init(rf_button_state *state, uint8_t pressed, uint32_t now_ms);
/* Returns 1 once per stable change after 30 ms; unsigned time handles wrap. */
uint8_t rf_button_update(rf_button_state *state, uint8_t pressed, uint32_t now_ms);
#endif
