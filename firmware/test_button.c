/* Host checks exercise bounce, stable hold, release and millisecond wrap. */
#include "rf_button.h"
#include <stdio.h>
#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr, "Button test failed at line %d\n", __LINE__); return 1; \
} } while (0)
int main(void)
{
    rf_button_state state;
    rf_button_init(&state, 0u, 0u);
    CHECK(!rf_button_update(&state, 1u, 10u));
    CHECK(!rf_button_update(&state, 0u, 15u));
    CHECK(!rf_button_update(&state, 1u, 20u));
    CHECK(!rf_button_update(&state, 1u, 49u));
    CHECK(rf_button_update(&state, 1u, 50u));
    CHECK(state.stable == 1u);
    CHECK(!rf_button_update(&state, 1u, 200u));
    CHECK(!rf_button_update(&state, 0u, UINT32_C(0xfffffff0)));
    CHECK(!rf_button_update(&state, 0u, UINT32_C(0x0000000d)));
    CHECK(rf_button_update(&state, 0u, UINT32_C(0x0000000e)));
    CHECK(state.stable == 0u);
    puts("Button bounce, hold, release and timer-wrap checks passed");
    return 0;
}
