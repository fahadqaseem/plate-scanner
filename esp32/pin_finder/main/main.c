/* Prints which candidate GPIOs go HIGH. Inputs with pull-down, so a floating
 * pin reads low and only a driven pin shows up. Press the FREE-WILi button. */
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"

/* Excludes pins the P4-EYE BSP uses (buttons, camera, LCD, I2C, I2S, knob, C6, SD, USB). */
static const int pins[] = {0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54};
#define N (sizeof pins / sizeof pins[0])

void app_main(void)
{
    for (int i = 0; i < N; i++) {
        gpio_config_t c = { .pin_bit_mask = 1ULL << pins[i], .mode = GPIO_MODE_INPUT,
                            .pull_down_en = GPIO_PULLDOWN_ENABLE };
        gpio_config(&c);
    }
    int last[N] = {0};
    printf("\npin_finder ready: press the FREE-WILi green button\n");
    while (1) {
        for (int i = 0; i < N; i++) {
            int v = gpio_get_level(pins[i]);
            if (v != last[i]) { printf("GPIO%d -> %d\n", pins[i], v); last[i] = v; }
        }
        vTaskDelay(1);
    }
}
