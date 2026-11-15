/* Display CPU: watch the green button, tell main to fire the pulse.
   Also keeps servicing main's breakout-direction requests (0x20-0x22),
   without which main's fwog_io_dir_apply() would time out. */
#include "fwog_display.h"
#include "io_expander/ioexp_link.h"
#include "pico/stdlib.h"
#include "platescan_proto.h"

FWOG_POWER_DEFAULT();   /* red held 6 s powers the board off */

static fwog_link_rx_t s_rx;   /* ~4 KB: must be file-scope, not on the stack */

int main(void) {
    board_init();
    const bool link_ok = fwog_link_uart_init(FWOG_LINK_BAUD);
    fwog_link_rx_init(&s_rx);
    DIAG("[platescan_display] link=%s\n", link_ok ? "ok" : "FAILED");

    uint8_t seq = 0;
    while (true) {
        const uint32_t now = to_ms_since_boot(get_absolute_time());
        const fwog_buttons_t b = fwog_power_poll(now).buttons;

        if ((b.pressed & FWOG_BTN_BIT(FWOG_BTN_GREEN)) && link_ok) {
            platescan_trigger_t m = { PLATESCAN_MSG_TRIGGER, ++seq };
            (void)fwog_link_uart_send_frame(&m, sizeof m);
            DIAG("[platescan_display] trigger %u\n", (unsigned)seq);
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
