/* Link message for the plate-scanner trigger. Display -> main.
   0x30 is in the application range (>= 0x20); 0x20-0x22 are the breakout I/O
   messages and must not be reused. */
#ifndef PLATESCAN_PROTO_H
#define PLATESCAN_PROTO_H
#include <stdint.h>

#define PLATESCAN_MSG_TRIGGER 0x30u

typedef struct __attribute__((packed)) {
    uint8_t type;   /* PLATESCAN_MSG_TRIGGER */
    uint8_t seq;    /* informational */
} platescan_trigger_t;

/* Breakout pin driven HIGH on trigger. Main-CPU GPIO 27 = breakout header
   pin 3, an OUTPUT in the board's default direction config. */
#define PLATESCAN_PIN       27u
#define PLATESCAN_PULSE_MS  200u
#endif
