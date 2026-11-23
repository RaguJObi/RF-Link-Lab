#include "rf_button.h"

void rf_button_init(rf_button_state *state, uint8_t pressed, uint32_t now_ms)
{
    state->stable = pressed;
    state->candidate = pressed;
    state->candidate_since_ms = now_ms;
}

uint8_t rf_button_update(rf_button_state *state, uint8_t pressed, uint32_t now_ms)
{
    if (pressed != state->candidate) {
        state->candidate = pressed;
        state->candidate_since_ms = now_ms;
    }
    if (state->candidate != state->stable &&
        (uint32_t)(now_ms - state->candidate_since_ms) >= UINT32_C(30)) {
        state->stable = state->candidate;
        return 1u;
    }
    return 0u;
}
