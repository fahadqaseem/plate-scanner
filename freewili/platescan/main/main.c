/* Main CPU: on a trigger from the display, drive breakout GPIO 27 high for
   PLATESCAN_PULSE_MS, then low. Non-blocking, so the watchdog is kicked and
   the link serviced throughout the pulse. */
#include "fwog_main.h"
#include "pico/stdlib.h"
#include "platescan_proto.h"

FWOG_WATCHDOG_DEFAULT();

static fwog_link_rx_t s_rx;   /* ~4 KB: file-scope, not stack */

int main(void) {
    board_init();
    fwog_display_result_t d = fwog_display_update_run();
    DIAG("[platescan_main] display: %s\n", fwog_display_result_text(d));

    /* Make sure the FPGA, level shifter and pad all say "main drives GPIO 27".
       Needs the display app to answer over the link, so retry for a while. */
    fwog_io_cfg_t cfg;
    fwog_io_cfg_default(&cfg);   /* gpio27_out = true by default */
    bool dir_ok = false;
    for (int i = 0; i < 20 && !dir_ok; i++) {
        board_watchdog_kick();
        dir_ok = (fwog_io_dir_apply(&cfg) == FWOG_IO_OK);
        if (!dir_ok) sleep_ms(250);
    }
    DIAG("[platescan_main] iodir=%s\n", dir_ok ? "ok" : "FAILED");
    (void)fwog_io_pin_drive(PLATESCAN_PIN, false);

    fwog_link_rx_init(&s_rx);
    bool pulsing = false;
#ifdef PLATESCAN_SELFTEST
    absolute_time_t next_self = make_timeout_time_ms(3000);
#endif
    absolute_time_t pulse_end = nil_time;

    while (true) {
        board_watchdog_kick();

        uint8_t c;
        while (fwog_link_uart_read(&c)) {
            size_t len = 0;
            if (!fwog_link_rx_byte(&s_rx, c, &len)) continue;
            if (len >= sizeof(platescan_trigger_t) &&
                s_rx.buf[0] == PLATESCAN_MSG_TRIGGER && !pulsing) {
                if (fwog_io_pin_drive(PLATESCAN_PIN, true) == FWOG_IO_PIN_OK) {
                    pulsing = true;
                    pulse_end = make_timeout_time_ms(PLATESCAN_PULSE_MS);
                    DIAG("[platescan_main] pulse\n");
                }
            }
        }

#ifdef PLATESCAN_SELFTEST
        /* Walk every default-output breakout line, one 200 ms pulse each, in a
           fixed order, so a listener on the far side can map lines to pins. */
        if (!pulsing && time_reached(next_self)) {
            static const unsigned order[] = { 27 };
            static unsigned idx;
            const unsigned g = order[idx];
            const fwog_io_pin_result_t pr = fwog_io_pin_drive(g, true);
            DIAG("[platescan_main] selftest #%u gpio%u result=%d\n", idx + 1, g, (int)pr);
            sleep_ms(200);
            (void)fwog_io_pin_drive(g, false);
            idx = (idx + 1) % (sizeof order / sizeof order[0]);
            next_self = make_timeout_time_ms(800);
        }
#endif
        if (pulsing && time_reached(pulse_end)) {
            (void)fwog_io_pin_drive(PLATESCAN_PIN, false);
            pulsing = false;
        }
    }
}
