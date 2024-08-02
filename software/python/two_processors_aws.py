"""
This version used both processors, one to read and the other to do the main work. It simply talks
to an open source HiveMQ MQTT broker, and has no certs or security
"""
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
pico_led    = machine.Pin(25, machine.Pin.OUT)


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
MQTTPUBLISHED	= 11


# Shared state variables. Use the lock to change them
alarm_set = False
last_alarm = None
battery = None
psm = False
state = RESET
ccid = None
clock = None
last_command = None


def set_state(state_value):
    global state
    with lock:
        state = state_value


def get_state():
    global state
    with lock:
        return state


def reader():
    """
    Use a separate process thread to read messages from the modem
    :return: Never
    """
    global last_command
    
    handler = ModemState({})

    while True:
        # Look for anything coming from the modem
        if modem.any():
            try:
                line = modem.readline()
            except Exception as e:
                print("Error reading uart {}".format(str(e)))
                continue

            print(f'"{line}"')
            try:
                line = line.decode('utf-8','ignore')
            except Exception as e:
                print("Error:{} decoding line {}".format(str(e),line))
                continue

            # State changes from the modem start with +
            if line.startswith('+'):
                result = ""
                try:
                    status, result = line.split(':', 1)
                except ValueError as e:
                    print(f"Error:{str(e)} for {line}")
                else:
                    status = status.replace('+', '').strip()
                    if hasattr(handler, status):
                        func = getattr(handler, status)
                        func(result)

            # if BROM in message, it means the modem reset, This will happen after leaving PSM mode
            elif 'BROM' in line or 'RDY' in line:
                set_state(READY)

            # All commands will either come back with OK, ERROR: you can sync and wait, but not implemented
            elif 'OK' in line or 'ERROR' in line:
                with lock:
                    last_command = None

            elif line.startswith('>'):
                set_state(READING)
        else:
            time.sleep_ms(100)


class ModemState:

    _connect_handler = None
    _subscribe_handler = None
    _disconnect_handler = None
    _publish_handler = None

    def __init__(self, config):
        """
        """
        self._subscribe_handler  = config.get('on_subscribe')
        self._connect_handler    = config.get('on_connect')
        self._disconnect_handler = config.get('on_disconnect')
        self._publish_handler    = config.get('on_publish')
        pico_led.value(0)

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
                set_state(REGISTERED)

            # If it's a solicited response it will be <n><stat>
            elif len(result) >= 2:
                if int(result[1]) in (1, 5):
                    set_state(REGISTERED)

        except ValueError as e:
            print(f"ValueError:{e} for CEREG:{result}")

    ''' All capital letter functions are read returns from the modem e.g. +QCCID: '''
    def QCCID(self, result):
        """
        Get the ccid +QCCID: 89882280666027595366\r\n'
        :param result:  string of numbers as text for the SIM CCID :
        :return:
        """
        global ccid
        with lock:
            ccid = result.strip()

    def QMTOPEN(self, result):
        """
        Open MQTT host e.g. +QMTOPEN: <tcp connection>, <state>
        :param result: the string after :
        :return:
        """
        result = result.split(',')
        try:
            if int(result[1]) == 0:
                set_state(MQTTOPENED)
                print("Opened MQTT")
            else:
                set_state(MQTTNOTOPENED)
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
                set_state = MQTTCLOSED
                print("MQTT connecion closed")
                if self._disconnect_handler:
                    self._disconnect_handler(result)

            elif self._connect_handler:
                self._connect_handler(result)

        except ValueError as e:
            print(f"ValueError:{e} for QMTSTAT:{result}")

    def QMTCONN(self, result):
        """
        # +QMTCONN: <TCP_connectID>,<result>[,<ret_code>]
        :param result: the string after :
        :return:
        """
        result = result.split(',')
        try:
            if int(result[1]) == 3:
                set_state(MQTTCONNECTED)
                
                # print(f"MQTT connected")
                if self._connect_handler:
                    self._connect_handler(result)

            elif int(result[1]) in (1, 2):
                pass
            
            elif int(result[1]) == 4:
                set_state(MQTTDISCONNECT)

        except ValueError as e:
            print(f"ValueError:{e} for QMTCONN:{result}")

    def QMTPUB(self, result):
        """
        Result of a publish command e.g. +QMTPUB: 0,0,0\r\n'
        """
        result = result.split(',')
        if self._publish_handler:
            self._publish_handler(result)
        set_state(MQTTPUBLISHED)

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

    def QMTCLOSE(self,result):
        result = result.split(',')
        if int(result[1]) == 0:
            set_state(MQTTCLOSED)
    
    def QMTDISC(self,result):
        result = result.split(',')
        if int(result[1]) == 0:
            set_state(MQTTCLOSED)
        
    def CBC(self, result):
        """
        # Get the current battery level eg. +CBC: 0,0,3275 Battery level
        :param result: str: remaining return
        :return:
        """
        global battery
        result = result.split(',')
        try:
            volts = result[2].replace('\r\n', '').strip()
        except:
            volts  = result[0].replace('\r\n', '').strip()
        with lock:
            battery = volts

    def CCLK(self, result):
        """
        Get current time from network '+CCLK: 24/02/19,14:57:04-20\r\n'
        For some strange reason Quectel split timeszone up by 4 so -20 is really -5
        :param result: What is left after the command
        Get the current clock time from the network eg. b'+CCLK: 2023/03/09,14:02:31GMT-5\r\n'
        """
        global clock
        with lock:
            clock = result.replace('\r\n', '').strip()

    def QNBIOTEVENT(self, result):
        """ Unsolicited QNBIOT events, show the state of PSM
        """
        global psm
        print(time_str())
        if 'ENTER PSM' in result:
            with lock:
                psm = True

        elif 'EXIT PSM' in result:
            with lock:
                psm = False

    def IP(self, result):
        """
        IP status
        :param result:
        :return:
        """
        global ip_address
        if len(result.split('.')) == 4:
            ip_address = result

    def CGDCONT(self, result):
        """
        # "+CGDCONT: 1,"IPV4V6","iot.nb","30.2.17.172",0,0,0,,,,,,0,,0"
        :param result: the string after :
        :return:
        """
        result = result.split(',')
        try:
            ip_address = result[3].split('.')
            if len(ip_address) == 4:
                self.ip_address = result[3]
        except IndexError:
            pass

 

class BC66:
    """
    Basic functions of the BC66 modem to get it working
    """

    def __init__(self):
        self.read = _thread.start_new_thread(reader, ())
    
    def power_reset(self):
        pwr_reset.value(0)
        time.sleep_ms(500)
        pwr_reset.value(1)
        time.sleep_ms(500)
        pwr_reset.value(0)
        reset.value(0)
    
    def reset(self):
        """
        Reset the modem without powering down.
        """
        reset.value(1)
        time.sleep_ms(100)
        reset.value(0)
        time.sleep_ms(100)
        self.state = RESET

    def network_ready(self, timeout=None):
        """
        Determine if the modem is registered on the network
        :return: True
        """
        self.send_at('cereg=1')
        now = time.time()
        while True:
            if get_state() == REGISTERED:
                break

            self.send_at("cereg?")
            if timeout:
                t = now - time.time()
                if t > timeout:
                    return False
            time.sleep(1)
  
        return True

    def send_at(self, command, timeout=None):
        """
        Send an AT command to the modem
        :param command: str: the at command to send
        :param timeout: seconds to wait for a reply
        :return:
        """
        command = command.lower()
        if not command.startswith('at'):
            command = 'at+' + command
        command = command + '\r\n'
        modem.write(bytes(command, 'utf-8'))
        with lock:
            last_command = command
        time.sleep_ms(100)

    def mqtt(self, host='test.mosquitto.org', port=8883):
        """
        Set up the MQTT connection
        :return:
        """
        global ccid
        
        # Configure security level
        self.send_at('qsslcfg=0,0,"sslversion",3')
        self.send_at('qsslcfg=0,0,"seclevel",1')
        # self.send_at('qsslcfg=0,0,"debug",1')

        # Send the cert
        current_state = get_state()
        self.send_at('qsslcfg=0,0,"cacert"')
        
        while not get_state() == READING:
            time.sleep_ms(10)
            
        with open('mosquitto.org.crt','rb') as f:
            size = 0
            for l in f.readlines():
                size += modem.write(l)
                time.sleep_ms(100)

        modem.write(bytes([26]))
        time.sleep_ms(1000)

        self.send_at('qmtcfg="ssl",1,1,0,0')

        # Open the MQTT broker
        self.send_at(f'qmtopen=1,"{host}",{port}')
        while True:
            if get_state() in (MQTTOPENED, MQTTNOTOPENED):
                break
            utime.sleep_ms(1000)

        if get_state() == MQTTOPENED:
            command = 'qmtconn=1,"{}"'.format(ccid)
            self.send_at(command)
        else:
            return False

        while True:
            if get_state() in (MQTTCONNECTED, MQTTDISCONNECT):
                break
            self.send_at('QMTCONN?')
            utime.sleep_ms(1000)
            
        if get_state() == MQTTCONNECTED:
            return True
        else:
            return False

    def subscribe(self, topic):
        """
        Subscribe to the current topic
        :param topic: topic string to subscribe to
        :return: None
        """
        if get_state() == MQTTCONNECTED:
            self.send_at(f'qmtsub=1,1,"{topic}",0')

    def publish(self):
        """
        MQTT publish the current status
        :return:
        """
        global battery
        with lock:
            volts = battery

        msg = json.dumps({'ccid': ccid,
                          'alarm': alarm_set,
                          'temperature': temperature(),
                          'volts': volts,
                          'timestamp': time_str()})

        current_state = get_state()
        self.send_at('qmtpub=1,0,0,0,"device/state"'.format(msg))
        
        while not get_state() == READING:
            time.sleep_ms(10)
            
        modem.write(msg)
        modem.write(bytes([26]))
        while not get_state() == MQTTPUBLISHED:
            time.sleep(1)
        set_state(current_state)
        
    def close(self):
        """
        Close the current MQTT session
        :return:
        """
        #self.send_at('qmtdisc=0')
        self.send_at('qmtclose=1')
        while not get_state() == MQTTCLOSED:
            time.sleep_ms(500)
            

def main():
    global psm
    bc66 = BC66()
    #bc66.power_reset()
    #bc66.reset()


    while True:
        bc66.network_ready()
        commands = [
            'qsclk=0',							    # Turn off PSM while we send commands
            'cedrxs=0',					            # Turn of DRX
            'qnbiotevent=1,1',                      # Report PSM events
            'cpsms=1,,,"00100010","00100001"',		# PSM Mode. Wake for 1 minute, sleep 12 hours)
            'qledmode=1',							# Set the LED mode for the network 0= off
            'cclk?',                                # Get the time
            'qccid',                                # Get the ccid
            'cbc',                                  # Get the battery level
            ]
        
        for command in commands:
            bc66.send_at(command)
        
        if bc66.mqtt(host='test.mosquitto.org', port=8883):
            bc66.publish()
            bc66.close()

        print(time_str())
        bc66.send_at('qsclk?')
        
        while True:
            with lock:
                if psm:
                    break
            time.sleep(1)

        now = utime.time() + 42300
        while True:
            print("sleep @{}".format(time_str()))
            time.sleep(60)
   
            # machine.lightsleep(3600000)		# Sleep for an hour at a clip an interupt will cause it to wake
            if alarm_set:
                print('alarm')
                break

            t = utime.time()
            if t >= now:
                break

        print("wake up @{}".format(time_str()))


if __name__ == "__main__":
    main()



