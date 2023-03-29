#include <stdio.h>
#include <string.h>

#include "hardware/rtc.h"
#include "pico/stdlib.h"
#include "hardware/uart.h"
#include "hardware/irq.h"
#include "hardware/gpio.h"
#include "hardware/adc.h"
#include "pico/util/datetime.h"

#include "queue.h"
#include "hello.h"
#include "cert.h"

#define BUFFER_SIZE 1024

char modem_buffer[BUFFER_SIZE];
queue_t que = {0, 0, BUFFER_SIZE, modem_buffer};

// RX interrupt handler
void on_uart_rx() 
{
    while (uart_is_readable(MODEM)) 
    {
        uint8_t ch = uart_getc(MODEM);
        queue_write(&que, ch);
    }
}

bool alarmTrigger = false;
void alarm_callback(uint gpio, uint32_t events) {
    printf("Alarm %x\r\n", events);
    alarmTrigger = gpio_get(WATER_ALARM)?true:false;
    gpio_put(ALARM_LED, !alarmTrigger);
}

// Set up all the right pins 
void pin_setup(){
    gpio_init(PICO_LED);
    gpio_set_dir(PICO_LED, GPIO_OUT);

    gpio_init(WATER_ALARM);
    gpio_set_dir(WATER_ALARM, GPIO_IN);
    gpio_pull_up(WATER_ALARM);
    gpio_set_irq_enabled_with_callback(WATER_ALARM, GPIO_IRQ_EDGE_FALL|GPIO_IRQ_EDGE_RISE, true, &alarm_callback);
    printf("Alarm %d\r\n", gpio_get(WATER_ALARM));

    gpio_init(ALARM_LED);
    gpio_set_dir(ALARM_LED, GPIO_OUT);
    gpio_pull_up(ALARM_LED);
 
    gpio_init(RESET);
    gpio_set_dir(RESET, GPIO_OUT);
    gpio_pull_down(RESET);

    gpio_init(PWR_RESET);
    gpio_set_dir(PWR_RESET, GPIO_OUT);
    gpio_pull_down(PWR_RESET);

    gpio_init(PSM_EINT);
    gpio_set_dir(PSM_EINT, GPIO_OUT);
    gpio_pull_up(PWR_RESET);

    gpio_put(PWR_RESET, false);
    sleep_ms(500);
    gpio_put(PWR_RESET, true);
    sleep_ms(500);
    gpio_put(PWR_RESET, false);
    gpio_put(RESET, false);
}

// Set up reading the modem
void modem_setup()
{
    uart_init(MODEM, 115200);
    uart_set_baudrate(MODEM, 115200);
    uart_set_hw_flow(MODEM, false, false);
    uart_set_format(MODEM, 8, 1, UART_PARITY_NONE);
    uart_set_translate_crlf(MODEM, false); 
    uart_set_fifo_enabled(MODEM, true);

    gpio_set_function(MODEM_TX, GPIO_FUNC_UART);
    gpio_set_function(MODEM_RX, GPIO_FUNC_UART);

    irq_set_exclusive_handler(UART1_IRQ, on_uart_rx);
    irq_set_enabled(UART1_IRQ, true);


    // Now enable the UART to send interrupts - RX only
    uart_set_irq_enables(MODEM, true, false);
}

// Remove a character from a string
void removeChar(char *str, char garbage) {

    char *src, *dst;
    for (src = dst = str; *src != '\0'; src++) {
        *dst = *src;
        if (*dst != garbage) dst++;
    }
    *dst = '\0';
}

void nothing(char *result)
{

}

bool registered = false;
void cereg(char *result)
{
    // eg. +CEREG: 0,1\r\n'"

    // If it's a solicited response it will be <n>,<stat>
    char *stat = strchr(result, ',');
    
   // If it's an unsolicited response it will be 1 element <stat>
    if (stat != NULL)
        stat++;
    else
        stat = result++; // Skip the space 

    printf("stat = \'%c\' \r\n", *stat);
    if (stat[0] == '1' || stat[0] == '5')
    {
        printf("Registered\r\n");
        registered = true;
    }
    else
    {
        printf("Not Registered\r\n");
        registered = false;
    }
}

char ccidNumber[30];
void ccid(char *result)
{
    removeChar(result,'\r');
    removeChar(result,'\n');
    strcpy(ccidNumber, result);
}

char battery[30];
void cbc(char * result)
{
    removeChar(result,'\r');
    removeChar(result,'\n');
    strcpy(battery, result);
}

char currentTime[26];
void clock( char *result)
{
    // +CCLK: 2023/03/26,16:32:07GMT-4

    int y,m,d,h,n,s,gmt;
    sscanf(result, "%4d/%2d/%2d,%2d:%2d:%2dGMT%1d", &y, &m, &d, &h, &n, &s, &gmt);
    

    removeChar(result,'\r');
    removeChar(result,'\n');
    strcpy(currentTime, result);
    
    h = h - gmt;
    if (h <= 0)
    {
        h = 24-h;
        d -= 1;
    }

    datetime_t t = {
            .year  = y,
            .month = m,
            .day   = d,
            .hour  = h,
            .min   = n,
            .sec   = s,
            .dotw  = (d += m < 3 ? y-- : y - 2, 23*m/9 + d + 4 + y/4- y/100 + y/400)%7
    };
    printf("%d %d %d  %d:%d:%d",y, m, d, h, n, s);
    rtc_set_datetime(&t);

}

bool mqttOpened = false;
void qmtopen(char *result)
{
    if (result[2] == '0')
    {
        mqttOpened = true;
        printf("Opened\r\n", mqttOpened);
    }
    else
    {
        mqttOpened = false;
        printf("Not Opened\r\n", mqttOpened);
    }
}

void qmtclose(char *result)
{
    mqttOpened = false;
}

bool mqttConnected = false;
void qmtconn(char *result)
{
    if (result[2] == '0')
    {
        mqttConnected = true;
        printf("Connected\r\n", mqttConnected);
    }
    else
    {
        mqttConnected == false;
        printf("Not Connected", mqttConnected);
    }
}

bool mqttPublished = false;
void qmtpub(char *result)
{
    if (result[2] == '0')
    {
        mqttPublished = true;
        printf("Published\r\n", mqttConnected);
    }
    else
    {
        mqttPublished == false;
        printf("Published", mqttConnected);
    }
}

bool psmMode = false;
void qnbiotevent(char *result)
{
    if (strstr(result, "ENTER PSM") != NULL)
        psmMode = true;

    if (strstr(result, "EXIT PSM" ) != NULL)
        psmMode = false;

}

typedef struct
{
    char *command;
    void (*func)(char *);
} State;

#define STATUS_CMDS 15
const State STATE[STATUS_CMDS]= {
    {"+CEREG",       cereg      },
    {"+QCCID",       ccid       },
    {"+QMTOPEN",     qmtopen    },
    {"+QMTCONN" ,    qmtconn    },
    {"+QMTPUB",      qmtpub     },
    {"+QMTCLOSE",    qmtclose   },
    {"+MQSTAT",      nothing    },
    {"+QMTCLOSE",    nothing    },
    {"+QMTCONN" ,    qmtconn    },
    {"+MQTRECV",     nothing    },
    {"+CBC",         cbc        },
    {"+CCLK",        clock      },
    {"+QNBIOTEVENT", qnbiotevent},
    {"+IP",          nothing    },
    {"+CGDCONT",     nothing    }
};

// Send AT commnds to the modem
void send_at(char *command)
{
    char line[500];
    sprintf(line, "at+%s\r\n", command);
    for( int i = 0; i < strlen(line); i++)
        uart_putc(MODEM, line[i]);
}

// Use this to read reponses from the modem
char response_buffer[BUFFER_SIZE];
int response_index = 0;

// Handle reponses from the modem
bool handle_response(char *response)
{
    printf(response);
    
    if (strstr(response,"OK") != NULL)
        return true;
    
    if (strstr(response,"ERROR") != NULL)
        return true;
    
    if (strstr(response, "BROM") != NULL)
        return true;
    
    if (response[0] == '>')
    {
        for (int i=0; i<strlen(certificate); i++)
        {
            uart_putc(MODEM, certificate[i]);
            if (certificate[i] == '\n')
                sleep_ms(50);
        }
        uart_putc(MODEM, (u_int8_t)26);

    }

    // A status reply solicited or unsolicited returns with a + 
    if (response[0] == '+')
    {
        for (int i=0; i<STATUS_CMDS; i++)
        {
            if (strstr(response, STATE[i].command) !=  NULL)
            {
                // Send everything after the :
                char *rest = strchr(response, ':');
                STATE[i].func(&rest[2]);        // Remove the :space
                break;
            }
        }
    }
    return false;
}

# define NUM_COMMANDS 15
int cmd_index = 0;
const char *commands[NUM_COMMANDS] = {                      // Is the network registered, request <n><stat>
                "cereg=1",                                  // Is the network registered, request <n><stat>
                "qsclk=0",                                  // Turn off PSM while we send commands
                "cclk?",                                    // Get the time
                "qccid",                                    // Get the ccid
                "cbc",                                      // Get the battery level
                "qnbiotevent=1,1",                          // Report PSM events
                "cpsms=1,,,\"10100101\",\"00100001\"",      // Set PSM 5 minutes, 1 min active
                "qsslcfg=1,5,\"cacert\"",                   // Send a cert
                "qsslcfg=1,5,\"seclevel\",1",               // Set security to client cert (1 server cert required)
                "qmtcfg=\"ssl\",0,1,1,5",                   // Turn on SSL
                "qmtopen=0,\"test.mosquitto.org\",8883",    // Open the MQTT broker
                "qmtconn=0,\"watchible\"",                  // Connect to MQTT broker
                "qmtpub=0,0,0,0,\"device/state\",\"%s\"",   // Publish message
                "qmtclose=0",                               // Close the connection ( required for PSM mode )
                "qsclk=1"  };


char jsonMsg[200];
char *build_message(void)
{
    char *msg = "{\"ccid\":\"%s\",\"alarm\":false,\"temperature\":\"20\",\"volts\":\"%s\",\"timestamp\":\"%s\"}";
    sprintf(jsonMsg, msg, ccidNumber, battery, currentTime);      
    return jsonMsg;
}

char new_command[200];
int loop() {
  
    bool last_completed = false;    // Did the last command you send complete
    bool blink = false;
    bool waiting = false;
    char datetime_buf[256];


    while(true)
    {
        response_index = 0;
        char read_char = '\0';
        
        // Read a line from the queue. 
        while (read_char != '\n')
        {
            if (!queue_empty(&que))
            {
                read_char = queue_read(&que);   
                response_buffer[response_index++] = read_char;
                response_buffer[response_index] = '\0';
            }
        }
 
        // Handle what you get back if the response is OK or ERROR, its a reponse to the last command sent
        last_completed = handle_response(response_buffer);
        if (last_completed || waiting)
        {
            if (!registered)
            {
                send_at("cereg?");
                sleep_ms(2000);
                continue;
            }
            
            printf("cmd_index %d waiting %d %d %d\r\n", cmd_index, mqttOpened, mqttConnected, mqttPublished);

            if (cmd_index < NUM_COMMANDS)
            {
                if ((cmd_index == 11 && mqttOpened == false)||
                    (cmd_index == 12 && mqttConnected == false))
                {
                    waiting = true;
                    sleep_ms(100);
                }
                else
                {
                    waiting = false;
                    if (cmd_index == 12)
                    {  
                        char *msg = build_message();
                        sprintf(new_command, commands[cmd_index++], msg);
                        send_at(new_command);
                    }
                    else
                        send_at((char *)commands[cmd_index++]);
                }
            }
            else
            {
                printf("Done %d PSM:%s\r\n", cmd_index, psmMode?"true":"false");
            }
        }
        if (psmMode)
        {
            printf("PSM:%s\r\n", psmMode?"true":"false");
            datetime_t t;
            rtc_get_datetime(&t);
            datetime_to_str(datetime_buf, sizeof(datetime_buf), &t);
            printf("time:%s\r\n",datetime_buf);            
            absolute_time_t until = delayed_by_ms(get_absolute_time(), 240000);
            sleep_until(until);
            return 1;
        }
        sleep_ms(10);
    }
}


void main()
{
    rtc_init();
    setup_default_uart();
    modem_setup();
    pin_setup();

    while (true)
    {
        psmMode = false;
        registered = false;
        mqttOpened = false;
        mqttConnected = false;
        cmd_index = 0;

        loop();

    }
}