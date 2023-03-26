#define MODEM     uart1
#define BAUD_RATE 115200
#define DATA_BITS 8
#define STOP_BITS 1
#define PARITY    UART_PARITY_NONE

#define MODEM_TX 4      // GPIO4
#define MODEM_RX 5      // GPIO5

#define PICO_LED 25   // machine.Pin(25, machine.Pin.OUT) 
#define WATER_ALARM 2 // machine.Pin(2,  machine.Pin.IN, machine.Pin.PULL_UP)
#define ALARM_LED 3   // machine.Pin(3,  machine.Pin.OUT, machine.Pin.PULL_DOWN)
#define RESET 13      // machine.Pin(13, machine.Pin.OUT, machine.Pin.PULL_DOWN)
#define PWR_RESET 14  // machine.Pin(14, machine.Pin.OUT, machine.Pin.PULL_DOWN)
#define PSM_EINT 15   // machine.Pin(15, machine.Pin.OUT, machine.Pin.PULL_UP)
