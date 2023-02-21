import time
import json
import utime
import machine
import _thread
import picosleep

RESET = 0
READY = 1
REGISTERED = 2
MQTTOPEN = 3
MQTTCONNECTED = 4
MQTTFAIL = 10

lock = _thread.allocate_lock()

modem = machine.UART(0, 115200)
debug = machine.UART(1, 115200)

# These pin are defined on the Watchible board
water_alarm = machine.Pin(2, machine.Pin.IN, machine.Pin.PULL_UP)
alarm_led = machine.Pin(3, machine.Pin.OUT, machine.Pin.PULL_DOWN)
reset = machine.Pin(13, machine.Pin.OUT, machine.Pin.PULL_DOWN)
pwr_reset = machine.Pin(14, machine.Pin.OUT, machine.Pin.PULL_DOWN)
psm_eint = machine.Pin(15, machine.Pin.OUT, machine.Pin.PULL_UP)

alarm_set = False
last_alarm = None
done = False


def now():
    t = utime.localtime()
    try:
        s = f"{t[0]}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d}"
    except TypeError as e:
        return str(e)
    return s


def callback(p):
    """
    Alarm interrupt, wake up the modem
    :param p:
    """
    global last_alarm, alarm_set

    now = time.time()
    # Check to see it the alarm has gone off already in the last hour
    if not last_alarm or now - last_alarm > 3600:
        debug.write('Alarm\r\n')
        alarm_set = True

        # Trigger the modem to wake up
        psm_eint.value(0)
        time.sleep(1)
        psm_eint.value(1)
        last_alarm = now

# Set up the interupt for the alarm
water_alarm.irq(trigger=machine.Pin.IRQ_FALLING, handler=callback)


class BC66:
    status = RESET
    alarm = None
    ccid = None

    def __init__(self):
        self.read = _thread.start_new_thread(self.reader, ())

        # Toggle the reset pin to get the modem going
        pwr_reset.value(0)
        time.sleep(.5)
        pwr_reset.value(1)
        time.sleep(.5)
        pwr_reset.value(0)
        reset.value(0)

    def reset(self):
        self.state = READY

        while not self.state == REGISTERED:
            self.send_at("AT+CEREG?")
            time.sleep(1)

        with lock:
            self.state = READY
        return True

    def reader(self):
        """
        Read from the modem
        :return:
        """
        global done
        while True:
            if modem.any():
                try:
                    line = modem.readline()
                except Exception as e:
                    debug.write("error reading uart {}\r\n".format(str(e)))
                    continue

                try:
                    line = line.decode('utf-8')
                except Exception as e:
                    debug.write("error decoding line {}\r\n".format(str(e)))
                    continue

                
                # If we got a line of code process it
                if line:
                    debug.write(line)
                    # State changes from the modem start with +
                    if line.startswith('+'):
                        self.handle_state(line)
                    else:
                        if 'OK' in line or \
                           'ERROR' in line or \
                           'BROM' in line:
                           
                            with lock:
                                done = True

            else:
                time.sleep(1)

    def handle_state(self, line):
        """
        Manage new states from the modem
        :param line:
        :return:
        """
        new_line = line.replace('\r', '').replace('\n', '')

        try:
            command, result = new_line.split(':', 1)
        except ValueError:
            debug.write(f"ValueError {line} \r\n")
            return

        if "CEREG" in command:
            result = result.split(',')
            try:
                if int(result[1]) in [1, 5]:
                    with lock:
                        self.state = REGISTERED

            except ValueError:
                pass

        # +QCCID:
        elif "QCCID" in command:
            self.ccid = result.strip()

        # +QMTOPEN: <TCP_connectID>,<result>
        elif "QMTOPEN" in command:
            result = result.split(',')
            try:
                if int(result[1]) == 0:
                    self.state = MQTTOPEN
                else:
                    debug.write("Failed to open MQTT\r\n")
                    self.state = MQTTFAIL

            except ValueError:
                debug.write(f"ValueError: splitting result {result}")

        # +QMTSTAT: <TCP_connectID>,<err_ code> 1,2,3
        elif "QMTSTAT" in command:
            result = result.split(',')
            if int(result[1]) > 0:
                self.state = MQTTOPEN

        # +QMTCONN: <TCP_connectID>,<result>[,<ret_code>]
        elif "QMTCONN" in command:
            result = result.split(',')
            if int(result[1]) == 0:
                self.state = MQTTCONNECTED

        # +QMTRECV: 0,0,"device/status","it works"
        elif "QMTRECV" in command:
            result = result.split(',')
            line = result[3]
            debug.write(line)
        '''
        elif "QNBIOT" in command:
           pass
        
        elif "QCFG" in command:
           pass
        '''

    def send_at(self, command):
        """
        Send an AT command to the modem
        :param command:
        :return:
        """
        global done
        
        with lock:
            done = False
        
        #debug.write(f"sending {command}\r\n")
        command += '\r'
        command = bytes(command, 'utf-8')
        try:
            modem.write(bytes(command))
        except Exception as e:
            debug.write("Error:{e} writing {command}\r\n")
            return

        while not done:
            time.sleep(.1)
             

    @property
    def state(self):
        return self.status

    @state.setter
    def state(self, value):
        self.status = value
        
        
def temperature():
    adc = machine.ADC(4) 
    adc_voltage = adc.read_u16() * (3.3 / (65535))
    return str(27 - (adc_voltage - 0.706)/0.001721)
    

def mqtt(bc66):
    """
    Set up the MQTT connection
    :param bc66:
    :return:
    """
    global alarm_set

    bc66.send_at('AT+QMTOPEN=0,"broker.hivemq.com",1883')
    while not bc66.state in (MQTTOPEN, MQTTFAIL):
        utime.sleep(1)

    if not bc66.state == MQTTFAIL:
        bc66.send_at('AT+QMTCONN=0,"petes-alfa-kit"')
        while not bc66.state in (MQTTCONNECTED, MQTTFAIL):
            utime.sleep(1)

    if bc66.state == MQTTCONNECTED:

        bc66.send_at('AT+QMTSUB=0,1,"device/status",0')
        temp = temperature()

        msg = json.dumps({'ccid': bc66.ccid,
                          'alarm': alarm_set,
                          'temperature':temperature(),
                          'timestamp': now()})
        bc66.send_at('AT+QMTPUB=0,0,0,0,"device/state","{}"'.format(msg))

    bc66.send_at('AT+QMTCLOSE=0')


def main():
    global done
    
    debug.write("Ready\r\n")

    with lock:
        done = False
        
    bc66 = BC66()
    time.sleep(2)

    while True:
        # bc66.send_at("ATI")
        bc66.send_at("AT+CCLK?")
        bc66.send_at("AT+QCCID")
        bc66.reset()
        bc66.send_at('AT+CEREG=5')
        bc66.send_at('AT+QNBIOTEVENT=1,1')

        bc66.send_at('AT+CPSMS=1,,,"00100001","00100001"')
        bc66.send_at('AT+CEREG?')
        # bc66.send_at('AT+QSCLK?')
        # bc66.send_at('AT+CBC')
        bc66.send_at('AT+CGDCONT?')

        mqtt(bc66)

        # The BROM will reset the code
        with lock:
            done = False
        
        while True:
            with lock:
                if done:
                    break
            time.sleep(2)


if __name__ == "__main__":
    main()

