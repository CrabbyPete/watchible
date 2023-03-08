import time
import json
import utime
import machine
import _thread


# Create a lock to share states read from the modem
lock = _thread.allocate_lock()

# Use UART2 to talk to the BC66 modem
modem = machine.UART(1, 115200, timeout=100, timeout_char=100, rxbuf=3*1024, txbuf=3*1024)


# These pins are defined on the Watchible board
water_alarm = machine.Pin( 2, machine.Pin.IN,  machine.Pin.PULL_UP)
alarm_led   = machine.Pin( 3, machine.Pin.OUT, machine.Pin.PULL_DOWN)
reset       = machine.Pin(13, machine.Pin.OUT, machine.Pin.PULL_DOWN)
pwr_reset   = machine.Pin(14, machine.Pin.OUT, machine.Pin.PULL_DOWN)
psm_eint    = machine.Pin(15, machine.Pin.OUT, machine.Pin.PULL_UP)


RESET            = 2
REGISTERED       = 3
MQTTOPENED       = 4
MQTTNOTOPENED    = 5
MQTTCLOSED       = 6
MQTTCONNECTED    = 7
MQTTNOTCONNECTED = 8

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
        print('Alarm')
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
    Read the on board temperature
    :return:
    """
    adc = machine.ADC(4)
    adc_voltage = adc.read_u16() * (3.3 / (65535))
    return str(27 - (adc_voltage - 0.706)/0.001721)


class BC66:
    state =  None
    battery = None
    ccid = None
    ip_address = None
    last_command = None
    registered = False

    def __init__(self):
        pwr_reset.value(0)
        time.sleep(.5)
        pwr_reset.value(1)
        time.sleep(.5)
        pwr_reset.value(0)
        reset.value(0)
        self.state = RESET

    def report(self):
        """
        Report current state
        :return:
        """
        msg = json.dumps({'ccid': self.ccid,
                          'alarm': alarm_set,
                          'temperature': temperature(),
                          'volts': self.battery,
                          'timestamp': time_str()})
        return msg

    def CEREG(self, result):
        result = result.split(',')
        try:
            # If its greater than 2 its an unsolicited response
            if not len(result) > 2:
                if int(result[1]) in (1, 5):
                    self.state = REGISTERED
            else:
                if int(result[0]) in (1,5):
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

    def QMTOPEN(self, result):
        """

        :param result:
        :return:
        """
        result = result.split(',')
        try:
            if int(result[1]) == 0:
                self.state = MQTTOPENED
            else:
                self.state = MQTTNOTOPENED
                print("Failed to open MQTT")

        except ValueError as e:
            print(f"ValueError:{e} for QMTOPEN:{result}")


    def MQSTAT(self,result):
        """
        +QMTSTAT: <TCP_connectID>,<err_ code> 1,2,3
        :param result:
        :return:
        """
        result = result.split(',')
        try:
            if int(result[1]) > 0:
                self.state = MQTTCLOSED

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
            if int(result[1]) == 0:
                self.state = MQTTCONNECTED
            else:
                self.state = MQTTNOTCONNECTED
                print(f"Failed to connect to connect: {result[2]} {result[3]}")
        except ValueError as e:
            print(f"ValueError:{e} for QMTCONN:{result}")

    def MQTRECV(self,result):
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

    def QNBIOTEVENT(self, result):
        """
        An unsolicited event
        :param result:
        :return:
        """
        if "ENTER PSM" in result:
            self.psm = True
        else:
            self.psm = False

    def CBC(self, result):
        """
        # +CBC: 0,0,3275 Battery level
        :param result:
        :return:
        """
        result = result.split(',')
        self.battery = result[2]

    def QNBIOT(self, result):
        """ in command: # Indicate QNBIOT events, show the state of PSM
        """
        pass

    def IP(self, result):
        """
        IP status
        :param self:
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
            self.ip = result[3]

    def at(self,command):
        if not command.startswith('at'):
            command = 'at+'+command
        command = command + '\r'
        print(f'sending {command}')
        modem.write(bytes(command,'utf-8'))
        self.last_command = command

    def reader(self):
        if modem.any():
            data = modem.readline()
            print(data)
            try:
                data = data.decode('utf-8','ignore')
            except Exception as e:
                print(f"Error:{str(e)} reading data")
                return None

            if 'BROM' in data:
                self.state = RESET
                time.sleep(1)

            if 'OK' in data or 'ERROR' in data:
                self.last_command = None

            if data.startswith('+'):
                status, result = data.split(':',1)
                status = status.replace('+','').strip()
                if hasattr(self, status):
                    func = getattr(self, status)
                    func(result)

            return data
        else:
            return None


def main():
    bc66 = BC66()
    commands = ['ati',
                'qccid',
                'cclk?',
                'cbc',
                'cereg=5',
                'qnbiotevent=1,1',
                'cpsms=1,,,"00100001","00100001"',
                'qsslcfg=1,5,"cacert"',
                'qsslcfg=1,5,"seclevel",1',
                'qmtcfg="ssl",0,1,1,5',
                'qmtopen=0,"test.mosquitto.org",8883',
                'qmtconn=0,"{}"',
                'qmtpub=0,0,0,0,"device/state","{}"',
                'qmtclose=0'
                ]

    # Wait for the modem to register on the network
    while not bc66.state == REGISTERED:
        time.sleep(1)
        bc66.at('cereg?')
        while bc66.reader():
            pass

    while True:
        index = 0
        while index < len(commands):
            if not bc66.last_command:
                command = None
                if 'conn' in commands[index]:
                    if bc66.state == MQTTOPENED:
                        command = commands[index].format(bc66.ccid)
                    elif bc66.state == MQTTNOTOPENED:
                        index += 2

                elif 'pub' in commands[index]:
                    if bc66.state == MQTTCONNECTED:
                        command = commands[index].format(bc66.report())
                    elif bc66.state == MQTTNOTCONNECTED:
                        index +=1
                else:
                    command = commands[index]

                if command:
                    bc66.at(command)
                    index += 1

            while data := bc66.reader():
                if '>' in data:
                    with open('mosquitto.org.crt','rb') as f:
                        size = 0
                        for l in f.readlines():
                            size += modem.write(l)
                            time.sleep_ms(100)
                        print(f"wrote {size} bytes for cert")
                    modem.write(bytes([26]))

        while True:
            data = bc66.reader()
            if bc66.state == RESET:
                break
            time.sleep(1)




if __name__ == '__main__':
    main()