"""
This version used both processors, one to read and the other to do the main work.
"""
import re
import time
import json
import config
import machine
import _thread


MONITOR: bool   = False
PSM_SLEEP       = 21600   # 21600=6 hours 43200=12 hours
HOURS_SLEEP     = 6

RESTART         = 1
RESET           = 2
READING         = 3
REGISTERED      = 4
MQTTOPENED      = 5
MQTTNOTOPENED   = 6
MQTTCLOSED      = 7
MQTTCONNECTED   = 8
MQTTPUBLISHED   = 9


def logger(s):
    debug.write(s+'\r\n')


if MONITOR:
    debug = machine.UART(0, 115200, tx=machine.Pin(0), rx=machine.Pin(1))
    log = logger
else:
    log = print


# Create a lock to share states read from the modem
lock        = _thread.allocate_lock()

# Use UART1 to talk to the BC66 modem
modem       = machine.UART(1, 115200, timeout=100, timeout_char=100, rxbuf=3*1024, txbuf=3*1024)

# These pins are defined on the Watchible board
water_alarm = machine.Pin( 2, machine.Pin.IN,  machine.Pin.PULL_UP)
alarm_led   = machine.Pin( 3, machine.Pin.OUT, machine.Pin.PULL_DOWN)
reset_btn   = machine.Pin(13, machine.Pin.OUT, machine.Pin.PULL_DOWN)
pwr_reset   = machine.Pin(14, machine.Pin.OUT, machine.Pin.PULL_DOWN)
psm_eint    = machine.Pin(15, machine.Pin.OUT, machine.Pin.PULL_UP)
pico_led    = machine.Pin("LED", machine.Pin.OUT)


def time_str(set_to: str = None):
    """
    Return the current date and time as string
    :param: set_to str: set the local time from the modem time eg.24/03/03,16:33:55-20
    :return: str: datetime string
    """
    if set_to:
        rgx = re.compile("\D+")
        values = rgx.split(set_to)
        values = list(map(int, values))
        values[6] = 0
        values.insert(3, 0)
        rtc = machine.RTC()
        log(f"time values {values}")
        rtc.datetime(values)

    t = time.localtime()
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
    log('Alarm')

    now = time.time()
    # Check to see it the alarm has gone off already in the last hour
    if not last_alarm or now - last_alarm > 3600:
        log('Alarm')
        alarm_set = True

        # Trigger the modem to wake up
        psm_eint.value(0)
        time.sleep(1)
        psm_eint.value(1)
        last_alarm = now


# Set up the interrupt for the alarm
water_alarm.irq(trigger=machine.Pin.IRQ_FALLING, handler=callback)


def temperature(voltage='3300'):
    """
    Read the on-board temperature
    :return:
    """
    try:
        voltage = int(voltage)/1000
        adc = machine.ADC(4)
        adc_voltage = adc.read_u16() * (voltage/65535)
        log(f"volts:{str(adc_voltage)}")
        return str(27 - (adc_voltage - 0.706)/0.001721)
    except Exception as e:
        log(f"Error:{e} in temperature")
    else:
        return "unknown"



# Shared state variables. Use the lock to change them if shared with the reader
psm 		= False
cert 		= False
done        = False
alarm_set 	= False
die_signal  = False

ccid 		= None
clock 		= None
battery 	= None
last_alarm 	= None
ip_address 	= None

state 		= RESET
modem_model = "Quectel_BC660K-GL"


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
    global modem_model, done, die_signal

    handler = ModemState({})

    while not die_signal:
        # Look for anything coming from the modem
        if modem.any():
            try:
                line = modem.readline()
            except Exception as e:
                log("Error reading uart {}".format(str(e)))
                continue

            log(f'"{line}"')
            try:
                line = line.decode('utf-8', 'ignore')
            except Exception as e:
                log("Error:{} decoding line {}".format(str(e), line))
                continue

            # State changes from the modem start with +
            if line.startswith('+'):
                try:
                    status, result = line.split(':', 1)
                except ValueError as e:
                    log(f"Error:{str(e)} for {line}")
                else:
                    status = status.replace('+', '').strip()
                    if hasattr(handler, status):
                        func = getattr(handler, status)
                        func(result)

            # if BROM in message, it means the modem reset, This will happen after leaving PSM mode
            elif 'BROM' in line or 'RDY' in line:
                set_state(RESTART)

            elif 'Quectel' in line:
                with lock:
                    modem_model = line.replace('\r','').replace('\n','')
                log(modem_model)

            # All commands will either come back with OK, ERROR: you can sync and wait, but not implemented
            elif 'OK' in line or 'ERROR' in line:
                with lock:
                    done = True

            elif line.startswith('>'):
                set_state(READING)
        else:
            time.sleep_ms(100)

    # Die signal
    log("Reader closing")
    _thread.exit()


class ModemState:

    _connect_handler = None
    _subscribe_handler = None
    _disconnect_handler = None
    _publish_handler = None

    def __init__(self, config={}):
        """
        """
        self._subscribe_handler  = config.get('on_subscribe')
        self._connect_handler    = config.get('on_connect')
        self._disconnect_handler = config.get('on_disconnect')
        self._publish_handler    = config.get('on_publish')

    def CEREG(self, result):
        """
        Reply whether the modem is registered on the network
        There are 2 types solicited and unsolicited
        e.g.: +CEREG: 1,5\r\n' solicited
        You can loose a connection for an instance and still retain the MQTT setup if it comes back
        :param result:
        :return:
        """
        result = result.replace('\r\n', '').replace(' ','').split(',')

        try:
            # If it's an unsolicited response it will be 1 element <stat>
            if len(result) == 1 and int(result[0]) in (1, 5):
                set_state(REGISTERED)

            # If it's a solicited response it will be <n><stat>
            elif len(result) >= 2:
                if int(result[1]) in (1, 5):
                    set_state(REGISTERED)

                # 0 = stap trying 3 = registration denied
                elif int(result[1]) in (0,3):
                    set_state(RESTART)

        except ValueError as e:
            log(f"ValueError:{e} for CEREG:{result}")

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

    def CGMM(self,result):
        """
        Return the modem model
        :param result: modem model string eg Quectel_BC660K-GL
        """
        global modem_model
        modem_model = result.replace('\r','').replace('\n','')

    def QMTOPEN(self, result):
        """
        Open MQTT host e.g. +QMTOPEN: <tcp connection>, <state>
        :param result: -1 Failed to open network
                        0 Network opened successfully
        """
        result = result.split(',')
        try:
            if int(result[1]) == 0:
                set_state(MQTTOPENED)
                log("Opened MQTT")
            else:
                set_state(MQTTNOTOPENED)
                log("Failed to open MQTT")

        except ValueError as e:
            log(f"ValueError:{e} for QMTOPEN:{result}")

    def QMTSTAT(self, result):
        """
        Unsolicited MQTT status change +QMTSTAT: <TCP_connectID>,<err_ code> 1,2,3
        :param result: the string after :
        :return:
        """
        result = result.split(',')
        log(f"MQTT connection closed {result}")
        try:
            if int(result[1]) > 0:
                set_state(MQTTCLOSED)
                log("MQTT connection closed")
                if self._disconnect_handler:
                    self._disconnect_handler(result)

            elif self._connect_handler:
                self._connect_handler(result)

        except ValueError as e:
            log(f"ValueError:{e} for QMTSTAT:{result}")

    def QMTCONN(self, result):
        """
        # +QMTCONN: <TCP_connectID>,<result>[,<ret_code>]
        :param result:
                    1 MQTT is initial
                    2 MQTT is connecting
                    3 MQTT is connected
                    4 MQTT is disconnecting
        :return:
        """
        result = result.split(',')

        # If an unsolicited response +QMTCONN: <TCP_connectID>,<result>[,<ret_code>]
        if len(result) == 3:
            try:
                if int(result[2]) == 0:
                    set_state(MQTTCONNECTED)
                    log(f"MQTT connected")
                    if self._connect_handler:
                        self._connect_handler(result)
            except Exception as e:
                log(f"Error:{e} for QMTCONN:{result}")

        else:
            try:
                if int(result[1]) == 3:
                    set_state(MQTTCONNECTED)

                    # log(f"MQTT connected")
                    if self._connect_handler:
                        self._connect_handler(result)

                elif int(result[1]) in (1, 2):
                    pass

                elif int(result[1]) == 4:
                    set_state(MQTTCLOSED)

            except ValueError as e:
                log(f"ValueError:{e} for QMTCONN:{result}")

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
            log(line)
        except ValueError as e:
            log(f"ValueError:{e} for QMTRECV:{result}")

        if self._subscribe_handler:
            self._subscribe_handler(result)

    def QMTCLOSE(self, result):
        result = result.split(',')
        if int(result[1]) == 0:
            set_state(MQTTCLOSED)

    def QMTDISC(self, result):
        result = result.split(',')
        if int(result[1]) == 0:
            set_state(MQTTCLOSED)

    def CBC(self, result):
        """
        # Get the current battery level e.g. +CBC: 0,0,3275 Battery level
        :param result: str: remaining return
        :return:
        """
        global battery
        result = result.split(',')
        try:
            volts = result[2].replace('\r\n', '').strip()
        except (ValueError, IndexError) as e:
            volts  = result[0].replace('\r\n', '').strip()
        with lock:
            battery = volts
    
    def CTZE(self, result):
        """
        Unsolicited timezone reporting
        :param result: eg "-16",1,"2024/07/26,22:32:32"\r\n'"
        <timezone in quarters, daylightsavings, datetime"
        """

    def CCLK(self, result):
        """
        Get current time from network '+CCLK: 24/02/19,14:57:04-20\r\n'
        For some strange reason Quectel split timeszone up by 4 so -20 is really -5
        :param result: What is left after the command
        Get the current clock time from the network eg. b'+CCLK: 2023/03/09,14:02:31GMT-5\r\n'
        """
        global clock
        set_to = False
        with lock:
            clock = result.replace('\r\n', '').strip()
            if set_to:
                log(time_str(clock))

    def QNBIOTEVENT(self, result):
        """ Unsolicited QNBIOT events, show the state of PSM
        """
        global psm
        #log(time_str())
        if 'ENTER PSM' in result or 'ENTER DEEPSLEEP' in result:
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
            with lock:
                ip_address = result

    def CGDCONT(self, result):
        """
        # "+CGDCONT: 1,"IPV4V6","iot.nb","30.2.17.172",0,0,0,,,,,,0,,0"
        :param result: the string after :
        :return:
        """
        global ip_address
        result = result.split(',')
        try:
            ip_address = result[3].split('.')
            if len(ip_address) == 4:
                with lock:
                    ip_address = result[3]
        except IndexError:
            pass


class BC66:
    """
    Basic functions of the BC66 modem to get it working
    """
    global die_signal

    def __init__(self):
        die_signal = False
        try:
            self.read = _thread.start_new_thread(reader, ())
        except Exception as e:
            log(f"Error:{e} starting read thread")


    def __del__(self):
        die_signal = True

    def power_reset(self):
        pwr_reset.value(0)
        time.sleep_ms(500)
        pwr_reset.value(1)
        time.sleep_ms(500)
        pwr_reset.value(0)
        reset_btn.value(0)

    def reset(self):
        """
        Reset the modem and cause a boot, same as hitting the button on the modem
        """
        #psm_eint.value(1)
        reset_btn.value(1)
        time.sleep_ms(100)
        reset_btn.value(0)
        time.sleep_ms(100)
        set_state(RESET)

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

    def send_at(self, command, wait=False):
        """
        Send an AT command to the modem
        :param command: str: the at command to send
        :return:
        """
        global done

        if wait:
            with lock:
                done = False

        if not command.startswith('at'):
            command = 'at+' + command

        command = command + '\r'
        modem.write(bytes(command, 'utf-8'))
        if wait:
            while True:
                with lock:
                    if done:
                        break
                time.sleep(1)
        else:
            time.sleep_ms(100)

    def set_cert(self, name, fyle, context_id, connect_id):
        """
        Set up a cert
        :param name: name of the modem cert to set
        :param fyle: the path of the file to read
        :return:
        """
        current = get_state()

        self.send_at(f'qsslcfg={context_id},{connect_id},"{name}"')

        while not get_state() == READING:
            time.sleep_ms(10)

        with open(fyle, 'rb') as f:
            for line in f.readlines():
                modem.write(line)
                time.sleep_ms(100)

        modem.write(bytes([26]))
        time.sleep_ms(1000)
        set_state(current)

    def certificate(self, cacert, clientcert=None, clientkey=None):
        """
        Set up the AWS MQTT certificate
        :return:
        """
        log(f"'{modem_model}'")
        # BC66 starts with 1, BC660K starts with 0
        if modem_model == "Quectel_BC66":
            context_id = 1
            connect_id = 1
        else:
            context_id = 0
            connect_id = 0

        # Will return error in BC66
        self.send_at(f'qsslcfg={context_id},{connect_id},"sslversion",4')
        self.send_at(f'qsslcfg={context_id},{connect_id},"seclevel",2')
        #self.send_at('qsslcfg=0,0,"debug",4')

        self.set_cert('cacert', cacert, context_id, connect_id)

        if clientkey:
            self.set_cert('clientkey', clientkey, context_id, connect_id)

        if clientcert:
            self.set_cert('clientcert', clientcert, context_id, connect_id)

        return context_id, connect_id


    def mqtt(self, host, port, context_id, connect_id):
        """
        Connect to MQTT server
        :param host: host string
        :param port: host port
        :param context_id:
        :param connect_id:
        :return:
        """
        global ccid

        tcp_id = 1
        self.send_at(f'qmtcfg="ssl",{tcp_id},1,{context_id},{connect_id}')

        # Open the MQTT broker
        self.send_at(f'qmtopen={tcp_id},"{host}",{port}')
        for _ in range(120):
            s = get_state()
            if s in (MQTTOPENED, MQTTNOTOPENED):
                break
            time.sleep_ms(2000)
        else:
            return False

        if s == MQTTOPENED:
            command = f'qmtconn={tcp_id},"{ccid}"'
            self.send_at(command)
        else:
            return False

        for _ in range(120):
            s = get_state()
            if s in (MQTTCONNECTED, MQTTCLOSED):
                break
            self.send_at('QMTCONN?')
            time.sleep_ms(2000)

        if s == MQTTCONNECTED:
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

    def publish(self, wake_ups):
        """
        MQTT publish the current status
        :return:
        """
        global battery, clock
        with lock:
            volts = battery

        msg = json.dumps({'ccid': ccid,
                          'alarm': alarm_set,
                          'temperature': temperature(),
                          'volts': volts,
                          'timestamp': clock,
                          'wakes':str(wake_ups)})

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
        self.send_at('qmtclose=1')
        while not get_state() == MQTTCLOSED:
            time.sleep_ms(500)

def main():
    global alarm_set, modem
    pico_led.on()
    bc66 = BC66()
    bc66.reset()
    bc66.power_reset()
    time.sleep(1)
    pico_led.off()

    """ Use this to change network provider 
    bc66.send_at('cfun=0')
    bc66.send_at(f'qcgdefcont="IPV4V6","{config.apn}"')
    bc66.send_at('qcgdefcont?')
    bc66.send_at('cfun=1')
    """

    bc66.send_at('cgmm',True)
    context_id, connect_id = bc66.certificate(config.cacert, config.clientcert, config.clientkey)

    # Initialize
    commands = [
        'qledmode=0',                           # Turn off LED on network
        'qnbiotevent=1,1',                      # Report PSM events
        'qcfg="dsevent",1',                     # Turn on reporting events
        f'qpsms=30,{PSM_SLEEP}' if modem_model  == "Quectel_BC660K-GL" else 'cpsms=1,,,"00100001","00000000"',
        'qccid'
    ]
    for command in commands:
        bc66.send_at(command)

    # Loop forever
    wake_ups = 0  # Keep track how many times you wake so you can see a reboot
    while True:
        wake_ups += 1
        bc66.network_ready()
        pico_led.on()
        commands = [
            'qsclk=0',							 # Turn off PSM while we send commands
            'cclk?',                             # Get the time
            'cbc',                               # Get the battery level
            'qccid',
            'qpsms?',
            'qmtcfg="timeout",1,30,10,1'
        ]
        for command in commands:
            bc66.send_at(command)

        #bc66.certificate(1)
        ok = bc66.mqtt(config.host, config.port, context_id, connect_id)
        if ok:
            bc66.publish(wake_ups)
        else:
            log("MQTT Failed")
        bc66.close()

        #bc66.send_at('qsclk=1') # Turn PSM back on

        # Wait to enter PSM
        while True:
            with lock:
                if psm:
                    break
            time.sleep(1)

        # Turn the LED off
        pico_led.off()

        # Now wait to leave PSM
        for _ in range(HOURS_SLEEP):
            log(f"sleep @{time_str()}")
            with lock:
                if not psm:
                    break

            if PSM_SLEEP < 3600:
                machine.lightsleep(PSM_SLEEP*1000) # Sleep for an hour at a clip an interrupt will cause it to wake
            else:
                machine.lightsleep(3600*1000)

            if alarm_set:
                log('alarm triggered')
                break

        log(f"wake up @{time_str()}")
        set_state(RESET)


if __name__ == '__main__':
    while True:
        try:
            main()
        except Exception as e:
            log(f"Error:{e} in main")