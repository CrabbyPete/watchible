import time
import json
import utime
import machine

from config import host, cacert, clientkey, clientcert

# import _thread

# Create a lock to share states read from the modem
# lock = _thread.allocate_lock()

# Use UART2 to talk to the BC66 modem
modem = machine.UART(1, 115200, timeout=100, timeout_char=100, rxbuf=2*1024)

# These pins are defined on the Watchible board
pico_led 	= machine.Pin(25, machine.Pin.OUT)
water_alarm = machine.Pin( 2, machine.Pin.IN,  machine.Pin.PULL_UP)
alarm_led 	= machine.Pin( 3, machine.Pin.OUT, machine.Pin.PULL_DOWN)
reset 		= machine.Pin(13, machine.Pin.OUT, machine.Pin.PULL_DOWN)
pwr_reset 	= machine.Pin(14, machine.Pin.OUT, machine.Pin.PULL_DOWN)
psm_eint 	= machine.Pin(15, machine.Pin.OUT, machine.Pin.PULL_UP)



RESTART = 1
RESET = 2
REGISTERED = 3
MQTTOPENED = 4
MQTTNOTOPENED = 5
MQTTCLOSED = 6
MQTTCONNECTED = 7
MQTTCONNECTING = 8
MQTTNOTCONNECTED = 9
SLEEP = 1


def time_str():
    """
    Return the current date and time as string
    :return: str: datetime string
    """
    t = utime.localtime()
    try:
        s = f"{t[0]}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d}"
    except TypeError as e:
        return str(e)
    return s


alarm_set = False
last_alarm = None


def callback(p):
    """
    Alarm interrupt callback, wake up the modem by hitting the reset pin
    :param p:
    """
    global last_alarm, alarm_set

    now = time.time()

    # Check to see it the alarm has gone off already in the last hour
    if not last_alarm or now - last_alarm > 3600:
        print(f'Alarm: {water_alarm.value()}')
        alarm_set = True

        # Trigger the modem to wake up
        psm_eint.value(0)
        time.sleep(1)
        psm_eint.value(1)
        last_alarm = now


# Set up the interrupt for the alarm
water_alarm.irq(trigger=machine.Pin.IRQ_FALLING, handler=callback)


def temperature():
    """
    Read the on-board temperature
    :return:
    """
    adc = machine.ADC(4)
    adc_voltage = adc.read_u16() * (3.3 / 65535)
    return str(27 - (adc_voltage - 0.706) / 0.001721)


class BC66:
    psm = False
    brom = False
    ccid = None
    imei = None
    clock = time_str()
    state = None
    battery = None
    ip_address = None
    last_command = None

    def __init__(self):
        self.power_reset()

    def power_reset(self):
        pwr_reset.value(0)
        time.sleep_ms(500)
        pwr_reset.value(1)
        time.sleep_ms(500)
        pwr_reset.value(0)
        reset.value(0)
        self.state = RESET

    def reset(self):
        reset.value(1)
        time.sleep_ms(100)
        reset.value(0)

    def report(self):
        """
        Report current state
        :return:
        """
        msg = json.dumps({'ccid': self.ccid,
                          'imea': self.imei,
                          'alarm': True if water_alarm.value() == 0 else False,
                          'temperature': temperature(),
                          'volts': self.battery,
                          'timestamp': self.clock
                          })
        return msg

    def CEREG(self, result):
        result = result.replace('\r\n', '').split(',')
        try:
            # If it's an unsolicited response it will be 1 element <stat>
            if len(result) == 1 and int(result[0]) in (1, 5):
                self.state = REGISTERED

            # If it's a solicited response it will be <n><stat>
            elif len(result) == 2:
                if int(result[1]) in (1, 5):
                    self.state = REGISTERED

        except ValueError as e:
            print(f"ValueError:{e} for CEREG:{result}")

    def QCCID(self, result):
        """
        Get the ccid
        :param result:
        :return:
        """
        self.ccid = result.strip()

    def QCGSN(self, result):
        """
        Get the imei
        :param result:
        :return:
        """
        self.imei = result.strip()

    def QMTOPEN(self, result):
        """
        Open MQTT host
        :param result:
        :return:
        """
        result = result.split(',')
        try:
            if int(result[1]) == 0:
                self.state = MQTTOPENED
                print("Opened MQTT")
            else:
                self.state = MQTTNOTOPENED
                print("Failed to open MQTT")

        except ValueError as e:
            print(f"ValueError:{e} for QMTOPEN:{result}")

    def MQTSTAT(self, result):
        """
        +QMTSTAT: <TCP_connectID>,<err_ code> 1,2,3
        :param result:
        :return:
        """
        result = result.split(',')
        print(f"MQTT connecion closed {result}")
        try:
            if int(result[1]) > 0:
                self.state = MQTTCLOSED
                print("MQTT connecion closed")

        except ValueError as e:
            print(f"ValueError:{e} for QMTSTAT:{result}")

    def QMTCLOSE(self, result):
        result = result.split(',')
        if int(result[1]) == 0:
            self.state = REGISTERED

    def QMTCONN(self, result):
        """
        # +QMTCONN: <TCP_connectID>,<result>[,<ret_code>]
        :param result:
        :return:
        """
        result = result.split(',')
        try:
            if int(result[1]) == 3:
                self.state = MQTTCONNECTED
                print(f"MQTT connected")
                
            elif int(result[1]) in (1,2):
                self.state = MQTTCONNECTING
                print(f"MQTT connecting")
            
            else:
                self.state = MQTTNOTCONNECTED
                print(f"MQTT disconnected")
        
        except ValueError as e:
            print(f"ValueError:{e} for QMTCONN:{result}")

    def MQTRECV(self, result):
        """
        +QMTRECV: 0,0,"device/status","it works" If PSM sleeping this will not happen
        :param self:
        :param result:
        :return:
        """
        try:
            result = result.split(',')
            line = result[3]
            print(line)
        except ValueError as e:
            print(f"ValueError:{e} for QMTRECV:{result}")

    def CBC(self, result):
        """
        # Get the current battery level eg. +CBC: 0,0,3275 Battery level
        :param result: str: remaining return
        :return:
        """
        result = result.split(',')
        try:
            self.battery = result[2].replace('\r\n', '').strip()
        except:
            self.battery  = result[0].replace('\r\n', '').strip()


    def CCLK(self, result):
        """
        :param result: What is left after the command
        Get the current clock time from the network eg. b'+CCLK: 2023/03/09,14:02:31GMT-5\r\n'
        """
        self.clock = result.replace('\r\n', '').strip()

    def QNBIOTEVENT(self, result):
        """ in command: # Indicate QNBIOT events, show the state of PSM
        """
        if 'ENTER PSM' in result:
            self.psm = True

        elif 'EXIT PSM' in result:
            self.psm = False

    def IP(self, result):
        """
        IP status
        :param result:
        :return:
        """
        if len(result.split('.')) == 4:
            self.ip_address = result

    def CGDCONT(self, result):
        """
        # "+CGDCONT: 1,"IPV4V6","iot.nb","30.2.17.172",0,0,0,,,,,,0,,0"
        :param result:
        :return:
        """
        result = result.split(',')
        ip_address = result[3].split('.')
        if len(ip_address) == 4:
            self.ip_address = result[3]

    def at(self, command):
        """
        send the command and preceed with +AT and end with cr nl
        :param command:
        :return:
        """
        if not command.startswith('at'):
            command = 'at+' + command
        command = command + '\r\n'

        # print(f'sending {command}')
        modem.write(bytes(command, 'utf-8'))
        self.last_command = command

    def reader(self):
        if modem.any():
            data = modem.readline()
            print(data)
            try:
                data = data.decode('utf-8', 'ignore')
            except Exception as e:
                print(f"Error:{str(e)} reading data")
                return None

            # BROM come if the modem reboots
            if 'BROM' in data:
                self.state = RESTART
                self.brom = True

            elif 'OK' in data or 'ERROR' in data:
                self.last_command = None

            # Handle responses both solicited and unsolicited
            elif data.startswith('+'):
                try:
                    status, result = data.split(':', 1)
                except ValueError as e:
                    print(f"Error:{str(e)} for {data}")
                    status = data

                status = status.replace('+', '').strip()
                if hasattr(self, status):
                    func = getattr(self, status)
                    func(result)

            return data
        else:
            return None

    def wait_state(self, state, query):
        """
        Wait for a particular state
        :param state: The state
        :param query:
        :return:
        """
        while True:
            if self.state == state:
                return True

            if query:
                self.at(query)
                time.sleep(2)
                
            while self.reader():
                pass



def main():
    global alarm_set, modem
    bc66 = BC66()
    commands = ['qsclk=0',                              # Turn off PSM while we send commands
                'cclk?',                                # Get the time
                #               'qccid',                                # Get the ccid
                #               'cgsn',                                 # Get IMEI
                #               'cbc',                                  # Get the battery level
                #               'qnbiotevent=1,1',                      # Report PSM events
                #               'cpsms=1,,,"10100101","00100001"',		# Set PSM 5 minutes, 1 min active
                'qsslcfg=0,0',
                'qsslcfg=0,0,"sslversion",4',
                'qsslcfg=0,0,"seclevel",2',             # Set security to client cert (1 server cert required)
                'qsslcfg=0,0,"cacert"',                 # Send a root cert
                'qsslcfg=0,0,"clientcert"',             # Send a client cert
                'qsslcfg=0,0,"clientkey"',              # Send a client key
                'qmtcfg="ssl",0,1,0,0',                 # Turn on SSL
                'qmtopen=0,"{}",8883',                  # Open the MQTT broker
                'qmtconn=0,"{}"',                       # Connect to MQTT broker
                #'qmtpub=0,0,0,0,"device/state"',        # Publish message
                'qmtsub=0,1,"device/update",0',               
                #'qmtclose=0',                           # Close the connection ( required for PSM mode )
                #'qsclk=1'                               # Turn PSM back on
                ]

    # Loop forever
    while True:

        bc66.at('cereg=1')                              # Is the network registered, request <n><stat>
        pico_led.value(1)

        # Wait 60 seconds for the modem to register on the network
        bc66.wait_state(REGISTERED, 'cereg?')
        bc66.at('qccid')

        # Indicates we are talking to the modem ( this goes fast ) Don't use if measuring power
        alarm_led.value(1)

        # Send each command
        index = 0
        while index < len(commands):

            # Make sure there are no pending commands before sending the next
            if not bc66.last_command:
                command = None

                # If this is the open then fill in the host name
                if 'open' in commands[index]:
                    command = commands[index].format(host)

                # If this is qmtconnn, make sure we opened first
                elif 'conn' in commands[index]:
                    if not bc66.state == MQTTOPENED:
                        bc66.wait_state(MQTTOPENED, None)
                    command = commands[index].format(bc66.ccid)

                # If this is a qmtpub(lish), make sure we are connected
                elif 'pub' in commands[index]:
                    bc66.wait_state(MQTTCONNECTED,'qmtconn?')
                    command = commands[index].format(bc66.report())

                elif 'sub' in commands[index]:
                    bc66.wait_state(MQTTCONNECTED,'qmtconn?')
                    command = commands[index]

                # Just write the current command
                else:
                    command = commands[index]

                if command:
                    bc66.at(command)
                    index += 1

            # See what the modem returns after sending commands
            while data := bc66.reader():

                # If you sent the cert command send the cert a line at a time
                if '>' in data:
                    if bc66.state == MQTTCONNECTED:
                        modem.write(bc66.report())
                    else:
                        if 'cacert' in command:
                            with open(cacert,'rb') as f:
                                size = 0
                                for line in f.readlines():
                                    size += modem.write(line)
                                    time.sleep_ms(100)
                            print(f"wrote {size} bytes for cert")

                        elif 'clientcert' in command:
                            with open(clientcert,'rb') as f:
                                size = 0
                                for line in f.readlines():
                                    size += modem.write(line)
                                    time.sleep_ms(100)
                            print(f"wrote {size} bytes for clientkey")

                        elif 'clientkey' in command:
                            with open(clientkey,'rb') as f:
                                size = 0
                                for line in f.readlines():
                                    size += modem.write(line)
                                    time.sleep_ms(100)
                            print(f"wrote {size} bytes for clientkey")

                    # Cntrl Z indicates that its done writing
                    modem.write(bytes([26]))

        # Done sending commands, wait for the modem to tell its in PSM mode
        bc66.psm = False
        alarm_led.value(0)
        pico_led.value(0)

        # Make sure everything has been sent and wait for psm
        while not bc66.psm:
            bc66.reader()

        # Wait a little less than the PSM time. Try to sync lightsleep and PSM as close as possible
        machine.lightsleep(240000)  # In this case the sleep is 4 min. 30 secs. PSM is 5 min.
        bc66.state = RESET


if __name__ == '__main__':
    main()
            

