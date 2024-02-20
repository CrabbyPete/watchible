import time
import json
import utime
import machine
import uasyncio as asyncio

from config import host, port, cacert, clientkey, clientcert

# Use UART2 to talk to the BC66 modem
modem = machine.UART(1, 115200, timeout=100, timeout_char=100, rxbuf=2 * 1024)

# These pins are defined on the Watchible board
water_alarm = machine.Pin( 2, machine.Pin.IN, machine.Pin.PULL_UP)
alarm_led   = machine.Pin( 3, machine.Pin.OUT, machine.Pin.PULL_DOWN)
reset       = machine.Pin(13, machine.Pin.OUT, machine.Pin.PULL_DOWN)
pwr_reset   = machine.Pin(14, machine.Pin.OUT, machine.Pin.PULL_DOWN)
psm_eint    = machine.Pin(15, machine.Pin.OUT, machine.Pin.PULL_UP)
pico_led    = machine.Pin(25, machine.Pin.OUT)

# The current state of the modem
RESET           = 1
READY           = 2
REGISTERED      = 3
READING         = 4
MQTTOPENED      = 5
MQTTNOTOPENED   = 6
MQTTCLOSED      = 7
MQTTCONNECTED   = 8
MQTTCONNECTING  = 9
MQTTDISCONNECT  = 10


last_alarm = None
alarm_set = False


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


def temperature():
    """
    Read the on-board temperature
    :return:
    """
    adc = machine.ADC(4)
    adc_voltage = adc.read_u16() * (3.3 / 65535)
    return str(27 - (adc_voltage - 0.706) / 0.001721)


class MQTTClient:
    tcp_id = 0
    ccid = None
    clock = time_str()
    state = RESET
    tcp_id = 0
    battery = None

    ip_address = ""
    _last_command = None

    _connect_handler = None
    _subscribe_handler = None
    _disconnect_handler = None
    _publish_handler = None

    def __init__(self, config):
        """
        """
        self._subscribe_handler = config.get('on_subscribe')
        self._connect_handler = config.get('on_connect')
        self._disconnect_handler = config.get('on_disconnect')
        self._publish_handler = config.get('on_publish')

    def power_reset(self):
        """
        Reset the modem by powering down then up
        """
        pwr_reset.value(0)
        time.sleep_ms(1000)
        pwr_reset.value(1)
        time.sleep_ms(1000)
        pwr_reset.value(0)
        reset.value(0)
        self.state = RESET

    def reset(self):
        """
        Reset the modem without powering down.
        """
        reset.value(1)
        time.sleep_ms(1000)
        reset.value(0)
        time.sleep_ms(1000)
        self.state = RESET

    async def network(self, psm=False):
        """
        Make sure there is a network connection to NB-IOT cellular network
        :param psm: POWER SAVING MODE, do not use for MQTT.
        :return:
        """
        self.at('qccid')

        if not psm:
            self.at('qsclk=0') 							# Turn off PSM
        else:
            self.at('qnbiotevent=1,1')  				# Report PSM events
            self.at('cpsms=1,,,"00101100","00100001"')  # Set PSM 12 hours, 1 min active
            self.at('qsclk=1')


        while not self.state == REGISTERED:
            self.at('cereg?')
            await asyncio.sleep_ms(1000)

        await asyncio.sleep(1)

    def CEREG(self, result):
        """
        Reply whether the modem is registered on the network
        There are 2 types solicited and unsolicited
        e.g.: +CEREG: 1,5\r\n' solicited
        You can loose a connection for an instance and still retain the MQTT setup if it comes back
        :param result: the string after :
        :return:
        """
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
        Get the ccid +QCCID: 89882280666027595366\r\n'
        :param result:  string of numbers as text for the SIM CCID :
        :return:
        """
        self.ccid = result.strip()

    def QMTOPEN(self, result):
        """
        Open MQTT host e.g. +QMTOPEN: <tcp connection>, <state>
        :param result: the string after :
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
        Unsolicited MQTT status change +QMTSTAT: <TCP_connectID>,<err_ code> 1,2,3
        :param result: the string after :
        :return:
        """
        result = result.split(',')
        print(f"MQTT connection closed {result}")
        try:
            if int(result[1]) > 0:
                self.state = MQTTCLOSED
                print("MQTT connecion closed")
                if self._disconnect_handler:
                    self._disconnect_handler(result)

            elif self._connect_handler:
                self._connect_handler(result)

        except ValueError as e:
            print(f"ValueError:{e} for QMTSTAT:{result}")

    def QMTCLOSE(self, result):
        """
        Close the current MQTT connection.
        :param result:
        :return:
        """
        result = result.split(',')
        if int(result[1]) == 0:
            self.state = REGISTERED

    def QMTCONN(self, result):
        """
        # +QMTCONN: <TCP_connectID>,<result>[,<ret_code>]
        :param result: the string after :
        :return:
        """
        result = result.split(',')
        try:
            if int(result[1]) == 3:
                self.state = MQTTCONNECTED
                #print(f"MQTT connected")
                if self._connect_handler:
                    self._connect_handler(result)

            elif int(result[1]) in (1, 2):
                self.state = MQTTCONNECTING
                #print(f"MQTT connecting")

        except ValueError as e:
            print(f"ValueError:{e} for QMTCONN:{result}")
    
    def QMTPUB(self, result):
        """
        Result of a publish command e.g. +QMTPUB: 0,0,0\r\n'
        """
        result = result.split(',')
        if self._publish_handler:
            self._publish_handler(result)
            

    def QMTRECV(self, result):
        """
        +QMTRECV: 0,0,"device/status","it works" If PSM sleeping this will not happen
        :param result: the string after :
        :return:
        """
        try:
            result = result.split(',')
            line = result[3]
            print(line)
        except ValueError as e:
            print(f"ValueError:{e} for QMTRECV:{result}")

        if self._subscribe_handler:
            self._subscribe_handler(result)
    
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
        Get current time from network '+CCLK: 24/02/19,14:57:04-20\r\n'
        :param result: What is left after the command
        Get the current clock time from the network eg. b'+CCLK: 2023/03/09,14:02:31GMT-5\r\n'
        """
        self.clock = result.replace('\r\n', '').strip()

    def QNBIOTEVENT(self, result):
        """ Unsolicited QNBIOT events, show the state of PSM
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
        :param result: the string after :
        :return:
        """
        result = result.split(',')
        ip_address = result[3].split('.')
        if len(ip_address) == 4:
            self.ip_address = result[3]

    def at(self, command):
        """
        Send the command and precede with +AT and end with cr nl
        :param command: command string to send to modem
        :return: None
        """
        if not command.startswith('at'):
            command = 'at+' + command
        command = command + '\r\n'

        modem.write(bytes(command, 'utf-8'))
        self._last_command = command

    async def reader(self):
        """
        This is the main task that reads everything coming from the modem. It changes the state as
        needed, and should run as long as the modem is up.
        :return:
        """
        while True:
            if modem.any():
                data = modem.readline()
                print(data)
                try:
                    data = data.decode('utf-8', 'ignore')
                except Exception as e:
                    print(f"Error:{str(e)} reading data")
                    continue

                # Response to the last command
                if 'OK' in data or 'ERROR' in data:
                    continue

                if 'RDY' in data:
                    self.state = READY

                # If the modem is expecting to read some it will send the prompt >
                elif '>' in data:
                    self.state = READING

                # Handle responses both solicited and unsolicited
                elif data.startswith('+'):
                    result = ""
                    try:
                        status, result = data.split(':', 1)
                    except ValueError as e:
                        print(f"Error:{str(e)} for {data}")
                        status = data

                    status = status.replace('+', '').strip()
                    if hasattr(self, status):
                        func = getattr(self, status)
                        func(result)
                    
            else:
                await asyncio.sleep_ms(1000)

    async def wait_for(self, state, query=None, timeout=None):
        """
        Wait for a particular state
        :param state: The state you need to wait for
        :param query: optional at query command to get the current state
        :param timeout: optional timeout
        :return: True when state happens (note, could add a timeout )
        """
        while True:
            if self.state == state:
                return True

            if query:
                self.at(query)
                time.sleep(2)

            await asyncio.sleep_ms(1000)

    async def send_cert(self, current_state, cert_file):
        """
        Send the cert to the modem
        :param: current_state: current statue to restore
        :param cert_file: the file to open read and send to modem
        :return: None
        """
        await self.wait_for(READING)
        with open(cert_file, 'rb') as f:
            size = 0
            for line in f.readlines():
                size += modem.write(line)
                time.sleep_ms(100)
        print(f"wrote {size} bytes for cert")

        # Cntrl Z indicates that its done writing
        modem.write(bytes([26]))

        # Restore the previous state
        self.state = current_state

    async def ssl(self):
        """
        Set up the ssl parmeters to connect to AWS
        :return:
        """
        self.at('qsslcfg=0,0,"sslversion",4')
        self.at('qsslcfg=0,0,"seclevel",2')  # Set security to client cert (1 server cert required)

        self.at('qsslcfg=0,0,"cacert"')  # Send a root cert
        await self.send_cert(self.state, cacert)

        self.at('qsslcfg=0,0,"clientcert"')  # Send a client cert
        await self.send_cert(self.state, clientcert)

        self.at('qsslcfg=0,0,"clientkey"')  # Send a client key
        await self.send_cert(self.state, clientkey)

        self.at('qmtcfg="ssl",0,1,0,0')  # Turn on SSL for MQTT
        return

    async def open(self):
        """
        Open a connection to the host MQTT server
        :return:
        """
        command = f'qmtopen={self.tcp_id},"{host}",{port}'  # Open the MQTT broker
        self.at(command)
        await self.wait_for(MQTTOPENED)

    async def connect(self):
        """
        Connect to the MQTT server that is open
        :return:
        """
        if self.state < MQTTOPENED:
            await self.open()

        command = f'qmtconn={self.tcp_id},"{self.ccid}"'  # Connect to MQTT broker
        self.at(command)
        await self.wait_for(MQTTCONNECTED, 'qmtconn?')

    def publish(self, topic, message):
        """
        Publish a message to a topic
        :param topic: topic string
        :param message: string of message to send
        :return:
        """
        current_state = self.state
        command = f'qmtpub=0,0,0,0,"{topic}"'  # Publish message
        self.at(command)

        await self.wait_for(READING)
        modem.write(message)
        time.sleep_ms(100)

        # Cntrl Z indicates that it's done writing
        modem.write(bytes([26]))

        # Restore the previous state
        self.state = current_state

    def report(self):
        """
        Report current state
        :return:
        """
        self.at('cbc')  # Get the battery level
        time.sleep(1)

        self.at('cclk?')
        time.sleep(1)
        msg = json.dumps({'ccid': self.ccid,
                          'alarm': True if water_alarm.value() == 0 else False,
                          'temperature': temperature(),
                          'volts': self.battery,
                          'timestamp': time_str(),
                          })
        return msg

    async def subscribe(self, topic):
        """
        Subscribe to a specific topic
        :param topic: topic string
        :return:
        """
        command = f'qmtsub={self.tcp_id},1,"{topic}",0'
        self.at(command)
        await asyncio.sleep_ms(100)

    async def close(self):
        """
        Close the MQTT connection
        :return:
        """
        command = f'qmtclose={self.tcp_id}'
        self.at(command)
        await asyncio.sleep_ms(1000)

    @staticmethod
    def alarm_set():
        """
        Has the alarm been activated.
        :return: True if so, else False
        """
        return water_alarm.value() == 0
