/* Original NUCLEO-F446RE board application. See repository LICENSE.
 * Register addresses/fields: ST RM0390 and official stm32f446xx.h.
 * Board routing: ST UM1724 and MB1136 C.4 schematic; see FLASH-STM32.md.
 * This application is loaded by reset, not jumped into from a bootloader.
 */
#include <stdint.h>
#include "rf_telemetry.h"
#include "rf_button.h"

#define REG32(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define RCC_CR          REG32(0x40023800u)
#define RCC_CFGR        REG32(0x40023808u)
#define RCC_APB1RSTR    REG32(0x40023820u)
#define RCC_AHB1ENR     REG32(0x40023830u)
#define RCC_APB1ENR     REG32(0x40023840u)
#define FLASH_ACR       REG32(0x40023c00u)
#define GPIOA_MODER     REG32(0x40020000u)
#define GPIOA_OTYPER    REG32(0x40020004u)
#define GPIOA_OSPEEDR   REG32(0x40020008u)
#define GPIOA_PUPDR     REG32(0x4002000cu)
#define GPIOA_BSRR      REG32(0x40020018u)
#define GPIOA_AFRL      REG32(0x40020020u)
#define GPIOC_MODER     REG32(0x40020800u)
#define GPIOC_PUPDR     REG32(0x4002080cu)
#define GPIOC_IDR       REG32(0x40020810u)
#define USART2_SR       REG32(0x40004400u)
#define USART2_DR       REG32(0x40004404u)
#define USART2_BRR      REG32(0x40004408u)
#define USART2_CR1      REG32(0x4000440cu)
#define USART2_CR2      REG32(0x40004410u)
#define USART2_CR3      REG32(0x40004414u)
#define SYSTICK_CTRL    REG32(0xe000e010u)
#define SYSTICK_LOAD    REG32(0xe000e014u)
#define SYSTICK_VAL     REG32(0xe000e018u)
#define SCB_VTOR        REG32(0xe000ed08u)
#define CORE_HZ         UINT32_C(16000000)
#define FRAME_PERIOD_MS UINT32_C(100)
#define UART_TIMEOUT_MS UINT32_C(20)

static volatile uint32_t uptime_ms;

void SysTick_Handler(void)
{
    ++uptime_ms;
}

static void clock_init(void)
{
    RCC_CR |= UINT32_C(1); /* HSION; preserve factory HSI trim. */
    while ((RCC_CR & (UINT32_C(1) << 1)) == 0u) { }
    /* Select HSI first; only then change dividers / remove other clocks. */
    RCC_CFGR &= ~UINT32_C(3);
    while ((RCC_CFGR & UINT32_C(12)) != 0u) { }
    RCC_CFGR = 0u; /* AHB/APB1/APB2 DIV1, hence all buses 16 MHz. */
    RCC_CR &= ~((UINT32_C(1) << 24) | (UINT32_C(1) << 16));
    FLASH_ACR &= ~UINT32_C(15); /* Zero wait states at 16 MHz. */
    SCB_VTOR = UINT32_C(0x08000000);
    __asm volatile ("dsb\nisb" ::: "memory");
}

static void board_init(void)
{
    RCC_AHB1ENR |= (UINT32_C(1) << 0) | (UINT32_C(1) << 2);
    (void)RCC_AHB1ENR; /* Clock-enable readback before GPIO access. */
    GPIOA_BSRR = UINT32_C(1) << 21; /* LD2 off before selecting output. */
    GPIOA_MODER = (GPIOA_MODER & ~((UINT32_C(3) << 4) |
                    (UINT32_C(3) << 6) | (UINT32_C(3) << 10))) |
                    (UINT32_C(2) << 4) | (UINT32_C(2) << 6) |
                    (UINT32_C(1) << 10);
    GPIOA_OTYPER &= ~((UINT32_C(1) << 2) | (UINT32_C(1) << 3) |
                       (UINT32_C(1) << 5));
    GPIOA_OSPEEDR = (GPIOA_OSPEEDR & ~((UINT32_C(3) << 4) |
                     (UINT32_C(3) << 6) | (UINT32_C(3) << 10))) |
                     (UINT32_C(1) << 4) | (UINT32_C(1) << 6);
    GPIOA_PUPDR = (GPIOA_PUPDR & ~((UINT32_C(3) << 4) |
                   (UINT32_C(3) << 6) | (UINT32_C(3) << 10))) |
                   (UINT32_C(1) << 6); /* RX idle pull-up. */
    GPIOA_AFRL = (GPIOA_AFRL & ~((UINT32_C(15) << 8) |
                  (UINT32_C(15) << 12))) |
                  (UINT32_C(7) << 8) | (UINT32_C(7) << 12);
    GPIOC_MODER &= ~(UINT32_C(3) << 26); /* PC13 input. */
    GPIOC_PUPDR = (GPIOC_PUPDR & ~(UINT32_C(3) << 26)) |
                  (UINT32_C(1) << 26); /* Board B1 connects to GND. */
}

static void uart_init(void)
{
    RCC_APB1ENR |= UINT32_C(1) << 17;
    (void)RCC_APB1ENR;
    RCC_APB1RSTR |= UINT32_C(1) << 17;
    RCC_APB1RSTR &= ~(UINT32_C(1) << 17);
    USART2_CR1 = 0u;
    USART2_CR2 = 0u; /* One stop bit. */
    USART2_CR3 = 0u; /* No flow control, no DMA. */
    /* OVER8=0; rounded PCLK/baud = 139: actual 115107.9 baud at ideal HSI. */
    USART2_BRR = (CORE_HZ + UINT32_C(57600)) / UINT32_C(115200);
    USART2_CR1 = (UINT32_C(1) << 13) | (UINT32_C(1) << 3);
    /* Transmitter only. PA3 remains AF7, but no host commands are implemented. */
}

static uint8_t uart_send(const uint8_t *frame, size_t size)
{
    const uint32_t started = uptime_ms;
    size_t i;
    for (i = 0u; i < size; ++i) {
        while ((USART2_SR & (UINT32_C(1) << 7)) == 0u) {
            if ((uint32_t)(uptime_ms - started) >= UART_TIMEOUT_MS) return 0u;
        }
        USART2_DR = frame[i];
    }
    while ((USART2_SR & (UINT32_C(1) << 6)) == 0u) {
        if ((uint32_t)(uptime_ms - started) >= UART_TIMEOUT_MS) return 0u;
    }
    return 1u;
}

static uint8_t button_pressed(void)
{
    return (GPIOC_IDR & (UINT32_C(1) << 13)) == 0u ? 1u : 0u;
}

int main(void)
{
    rf_button_state button;
    uint8_t frame[RF_TELEMETRY_FRAME_SIZE];
    uint8_t first_frame = 1u;
    uint8_t tx_error = 0u;
    uint32_t sequence = 0u;
    uint32_t last_periodic_ms = 0u;
    clock_init();
    board_init();
    uart_init();
    rf_button_init(&button, button_pressed(), 0u);
    SYSTICK_LOAD = CORE_HZ / UINT32_C(1000) - 1u;
    SYSTICK_VAL = 0u;
    SYSTICK_CTRL = 7u; /* Processor clock, exception enabled, counter enabled. */
    __asm volatile ("cpsie i" ::: "memory");

    for (;;) {
        const uint32_t now = uptime_ms;
        const uint8_t changed = rf_button_update(&button, button_pressed(), now);
        const uint8_t periodic = (uint32_t)(now - last_periodic_ms) >= FRAME_PERIOD_MS;
        if (first_frame || changed || periodic) {
            if (first_frame || periodic) last_periodic_ms = now;
            first_frame = 0u;
            if (rf_telemetry_encode(frame, sizeof(frame), sequence++, now,
                                    button.stable) != sizeof(frame) ||
                !uart_send(frame, sizeof(frame))) {
                tx_error = 1u;
                uart_init(); /* Next frame can recover after a timeout. */
            } else {
                tx_error = 0u;
            }
        }
        /* Normal: 1 Hz heartbeat; held B1: solid; UART timeout: fast blink. */
        if ((tx_error && ((now / UINT32_C(125)) & 1u)) ||
            (!tx_error && (button.stable || ((now / UINT32_C(500)) & 1u)))) {
            GPIOA_BSRR = UINT32_C(1) << 5;
        } else {
            GPIOA_BSRR = UINT32_C(1) << 21;
        }
        __asm volatile ("wfi");
    }
}
