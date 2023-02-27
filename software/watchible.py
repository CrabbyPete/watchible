import time
import json
import utime
import machine
import _thread


# Create a lock to share states read from the modem
lock = _thread.allocate_lock()

# Use UART2 to talk to the BC66 modem
modem = machine.UART(1, 115200)


# These pins are defined on the Watchible board
water_alarm = machine.Pin(2, machine.Pin.IN,   machine.Pin.PULL_UP)
alarm_led   = machine.Pin(3, machine.Pin.OUT,  machine.Pin.PULL_DOWN)
reset       = machine.Pin(13, machine.Pin.OUT, machine.Pin.PULL_DOWN)
pwr_reset   = machine.Pin(14, machine.Pin.OUT, machine.Pin.PULL_DOWN)
psm_eint    = machine.Pin(15, machine.Pin.OUT, machine.Pin.PULL_UP)

# Shared state varibles
alarm_set = False
last_alarm = None

battery = None
done = False


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



"""
States the board is in
"""
STARTED          = 1
RESET            = 2
REGISTERED       = 3
MQTTOPENED       = 4
MQTTNOTOPENED    = 5
MQTTCLOSED       = 6
MQTTCONNECTED    = 7
MQTTNOTCONNECTED = 8


class BC66:
    """
    Basic functions of the BC66 modem to get it working
    """
    ip         = None
    psm        = False
    ccid       = None
    alarm      = None
    status     = None
    registered = False

    def __init__(self):
        self.read = _thread.start_new_thread(self.reader, ())

    def power_reset(self):
        """
        # Toggle the reset pin to get the modem going
        :return:
        """
        # Toggle the reset pin to get the modem going
        pwr_reset.value(0)
        time.sleep(.5)
        pwr_reset.value(1)
        time.sleep(.5)
        pwr_reset.value(0)
        reset.value(0)
        self.state = STARTED

    def network_registered(self, timeout=None):
        """
        Determine if the modem is registered on the network
        :return: True
        """
        now = time.time()
        ready = False
        while not ready:
            with lock:
                ready = self.registered
            self.send_at("AT+CEREG?")
            if timeout:
                t = now - time.time()
                if t > timeout:
                    return False

            time.sleep(1)
        return True

    def reader(self):
        """
        Use a separate process thread to read messages from the modem
        :return: Never
        """
        global done
        while True:

            # Look for anything coming from the modem
            if modem.any():
                try:
                    line = modem.readline()
                except Exception as e:
                    print("Error reading uart {}".format(str(e)))
                    continue

                try:
                    line = line.decode('utf-8')
                except Exception as e:
                    print("Error decoding line {}".format(str(e)))
                    continue

                # If we got a line of code process it
                if line:
                    print(line)

                    # State changes from the modem start with +
                    if line.startswith('+'):
                        self.handle_state(line)

                    # if BROM in message, it means the modem reset, This will happen after leaving PSM mode
                    elif 'BROM' in line:
                        with lock:
                            self.state = RESET

                    # All commands will either come back with OK, ERROR
                    elif 'OK' in line or 'ERROR' in line:
                        with lock:
                            done = True
            else:
                time.sleep(.5)

    def handle_state(self, line):
        """
        Manage new states from the modem
        :param line:
        :return:
        """
        global battery

        line = line.replace('\r', '').replace('\n', '')

        try:
            command, result = line.split(':', 1)
        except ValueError as e:
            print(f"ValueError {str(e)} in {line}")
            return

        # +CEREG can come unsolicited as states change, especially if using PSM mode
        if "+CEREG" in command:
            result = result.split(',')
            try:
                # If its greater than 2 its an unsolicited response
                if not len(result) > 2:
                    if int(result[1]) in (1, 5):
                        with lock:
                            self.registered = True

            except ValueError as e:
                print(f"ValueError:{e} for CEREG:{result}")

        # +QCCID: Get the ccid on the SIM
        elif "QCCID" in command:
            self.ccid = result.strip()

        # +QMTOPEN: <TCP_connectID>,<result>
        elif "QMTOPEN" in command:
            result = result.split(',')
            try:
                if int(result[1]) == 0:
                    self.state = MQTTOPENED
                else:
                    self.state = MQTTNOTOPENED
                    print("Failed to open MQTT")

            except ValueError as e:
                print(f"ValueError:{e} for QMTOPEN:{result}")

        # +QMTSTAT: <TCP_connectID>,<err_ code> 1,2,3
        elif "QMTSTAT" in command:
            result = result.split(',')
            try:
                if int(result[1]) > 0:
                    self.state = MQTTCLOSED

            except ValueError as e:
                print(f"ValueError:{e} for QMTSTAT:{result}")

        # +QMTCONN: <TCP_connectID>,<result>[,<ret_code>]
        elif "QMTCONN" in command:
            result = result.split(',')
            try:
                if int(result[1]) == 0:
                    self.state = MQTTCONNECTED
                else:
                    self.state = MQTTNOTCONNECTED
                    print(f"Failed to connect to connect: {result[2]} {result[3]}")
            except ValueError as e:
                print(f"ValueError:{e} for QMTCONN:{result}")

        # +QMTRECV: 0,0,"device/status","it works" If PSM sleeping this will not happen
        elif "QMTRECV" in command:
            try:
                result = result.split(',')
                line = result[3]
                print(line)
            except ValueError as e:
                print(f"ValueError:{e} for QMTRECV:{result}")

        elif "+QNBIOTEVENT" in command:
            if "ENTER PSM" in result:
                self.psm = True
            else:
                self.psm = False
        
        # +CBC: 0,0,3275 Battery level
        elif "CBC" in command:
            result = result.split(',')
            battery = result[2]

        elif "QNBIOT" in command: # Indicate QNBIOT events, show the state of PSM
            pass

        elif "QCFG" in command:
            pass
        
        elif "+IP" in command:
            if len(result.split('.')) > 3:
                self.ip = result

    def send_at(self, command, timeout=None):
        """
        Send an AT command to the modem
        :param command: str: the at command to send
        :param timeout: seconds to wait for a reply
        :return:
        """
        global done

        with lock:
            done = False

        command += '\r'
        command = bytes(command, 'utf-8')
        try:
            modem.write(bytes(command))
        except Exception as e:
            print(f"Error:{e} writing {command}")
            return

        while not done:
            time.sleep(.1)

    @property
    def state(self):
        return self.status

    @state.setter
    def state(self, value):
        self.status = value

    def mqtt(self):
        """
        Set up the MQTT connection
        :return:
        """
        global alarm_set, battery

        # Open the MQTT broker
        self.send_at('AT+QMTOPEN=0,"broker.hivemq.com",1883')
        while not self.state in( MQTTOPENED, MQTTNOTOPENED):
            utime.sleep(1)

        if self.state == MQTTOPENED:
            self.send_at('AT+QMTCONN=0,"petes-alfa-kit"')
            while not self.state in (MQTTCONNECTED, MQTTNOTCONNECTED):
                utime.sleep(1)

        if self.state == MQTTCONNECTED:
            self.send_at('AT+QMTSUB=0,1,"device/status",0')
            temp = temperature()

            msg = json.dumps({'ccid': self.ccid,
                              'alarm': alarm_set,
                              'temperature':temperature(),
                              'volts':battery,
                              'timestamp': time_str()})
            self.send_at('AT+QMTPUB=0,0,0,0,"device/state","{}"'.format(msg))

        # You have to close the connection or you won't be able enter PSM
        self.send_at('AT+QMTCLOSE=0')


