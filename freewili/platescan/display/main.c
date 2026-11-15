/* Display CPU: watch the green button, tell main to fire the pulse.
   Also keeps servicing main's breakout-direction requests (0x20-0x22),
   without which main's fwog_io_dir_apply() would time out. */
#include "fwog_display.h"
#include "io_expander/ioexp_link.h"
#include "hardware/pio.h"
#include "pico/stdlib.h"
#include "platescan_proto.h"

FWOG_POWER_DEFAULT();   /* red held 6 s powers the board off */

static fwog_link_rx_t s_rx;   /* ~4 KB: must be file-scope, not on the stack */

#define IDLE_STEP_MS  120u   /* slow blue comet = "ready" */
#define FLASH_MS      1000u   /* all-green flash after a trigger */

static bool lcd_ok;

/* Draw `str` horizontally centred at row y, using the largest scale (<= max_scale) that fits. */
static void draw_centered(uint16_t y, const char *str, unsigned max_scale, uint16_t fg, uint16_t bg) {
    unsigned n = 0;
    while (str[n]) n++;
    unsigned scale = max_scale;
    while (scale > 1u && lcd_text_width_px(n, scale) > ST7789_W - 10u) scale--;
    const unsigned w = lcd_text_width_px(n, scale);
    lcd_text_draw((uint16_t)((ST7789_W - w) / 2u), y, str, scale, fg, bg);
}

static void screen_ready(void) {
    if (!lcd_ok) return;
    const uint16_t bg = st7789_rgb565(0, 0, 40);
    st7789_clear(bg);
    draw_centered(50, "FREE MILES", 5, st7789_rgb565(255, 255, 255), bg);
    draw_centered(120, "TEAM ZORRO", 4, st7789_rgb565(255, 180, 0), bg);
    draw_centered(200, "press green = photo", 2, st7789_rgb565(120, 200, 255), bg);
}

static void screen_flash(void) {
    if (!lcd_ok) return;
    const uint16_t g = st7789_rgb565(0, 220, 0), k = st7789_rgb565(0, 0, 0);
    st7789_clear(g);
    lcd_text_draw(60, 100, "PHOTO!", 5, k, g);
}

static void leds_fill(uint8_t r, uint8_t g, uint8_t b) {
    for (unsigned i = 0; i < FWOG_LED_COUNT; ++i) ws2812_set_color(i, r, g, b);
    ws2812_process();
}

static void leds_idle(unsigned head) {
    static const uint8_t trail[FWOG_LED_COUNT] = {60u, 28u, 12u, 5u, 2u, 1u, 0u};
    for (unsigned i = 0; i < FWOG_LED_COUNT; ++i)
        ws2812_set_color(i, 0u, 0u, trail[(head + FWOG_LED_COUNT - i) % FWOG_LED_COUNT]);
    ws2812_process();
}

int main(void) {
    board_init();
    st7789_init_begin();
    for (absolute_time_t d = make_timeout_time_ms(500); !st7789_ready() && !time_reached(d);) {
        st7789_init_step();
        sleep_ms(1);
    }
    lcd_ok = st7789_ready();
    screen_ready();
    const bool leds_ok = ws2812_init(pio0, 0u);
    if (leds_ok) leds_fill(0u, 80u, 0u);   /* green on boot */
    absolute_time_t next_led = make_timeout_time_ms(FLASH_MS);
    unsigned head = 0u;
    bool flashing = true;
    const bool link_ok = fwog_link_uart_init(FWOG_LINK_BAUD);
    fwog_link_rx_init(&s_rx);
    DIAG("[platescan_display] link=%s\n", link_ok ? "ok" : "FAILED");

    uint8_t seq = 0;
    while (true) {
        const uint32_t now = to_ms_since_boot(get_absolute_time());
        const fwog_power_t power = fwog_power_poll(now);
        const fwog_buttons_t b = power.buttons;

        if ((b.pressed & FWOG_BTN_BIT(FWOG_BTN_GREEN)) && link_ok) {
            platescan_trigger_t m = { PLATESCAN_MSG_TRIGGER, ++seq };
            (void)fwog_link_uart_send_frame(&m, sizeof m);
            DIAG("[platescan_display] trigger %u\n", (unsigned)seq);
            if (leds_ok) {
                leds_fill(0u, 255u, 0u);   /* bright green flash */
                screen_flash();
                flashing = true;
                next_led = make_timeout_time_ms(FLASH_MS);
            }
        }

        if (leds_ok && !power.armed && time_reached(next_led)) {
            if (flashing) { flashing = false; screen_ready(); next_led = make_timeout_time_ms(IDLE_STEP_MS); }
            else { next_led = delayed_by_ms(next_led, IDLE_STEP_MS); head = (head + 1u) % FWOG_LED_COUNT; }
            leds_idle(head);
        }

        uint8_t c;
        while (link_ok && fwog_link_uart_read(&c)) {
            size_t len = 0;
            if (fwog_link_rx_byte(&s_rx, c, &len))
                (void)fwog_ioexp_link_handle(s_rx.buf, len);
        }
        sleep_ms(2);
    }
}
