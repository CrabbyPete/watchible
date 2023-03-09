

"""
Tho original device used UART1 to talk to the modem, and UART2 to talk to the PICO. This was changed in
the next version of the device, but switching the UARTS on the latest it will still work
"""
import time
import json
import utime
import machine
import _thread


RESET = 0
READY = 1
REGISTERED = 2
MQTTOPEN = 3
MQTTCONNECTED = 4


lock = _thread.allocate_lock()

modem = machine.UART(0, 115200)
debug = machine.UART(1, 115200)

# These pin are defined on the Watchible board
water_alarm = machine.Pin( 2, machine.Pin.IN,  machine.Pin.PULL_UP)
alarm_led   = machine.Pin( 3, machine.Pin.OUT, machine.Pin.PULL_DOWN)
reset       = machine.Pin(13, machine.Pin.OUT, machine.Pin.PULL_DOWN)
pwr_reset   = machine.Pin(14, machine.Pin.OUT, machine.Pin.PULL_DOWN)
psm_eint    = machine.Pin(15, machine.Pin.OUT, machine.Pin.PULL_UP)


def now():
    t = utime.localtime()
    try:
        s = f"{t[0]}-{t[1]}-{t[2]} {t[3]}:{t[4]}:{t[5]}"
    except TypeError as e:
        return str(e)
    return s


alarm_set = False
last_alarm = None


def callback(p):
    """
    Alarm interupt, wake up the modem
    :param p:
    """
    global last_alarm, alarm_set

    now = time.time()
    if not last_alarm or now - last_alarm > 3600:

        debug.write('Alarm\r\n')
        alarm_set = True

        psm_eint.value(0)
        time.sleep(1)
        psm_eint.value(1)
        last_alarm = now


water_alarm.irq(trigger=machine.Pin.IRQ_FALLING, handler=callback)


lines = []


class BC66:
    status = RESET
    alarm = None
    ccid = None

    def __init__(self):
        self.read = _thread.start_new_thread(self.reader,())

        # Hit the reset pin to get the modem going
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

                if line:
                    if line.startswith('+'):
                        self.handle_state(line)
                    else:
                        line.replace('\r','').replace('\n','')
                        with lock:
                            lines.append(line)
            else:
                time.sleep(1)

    def handle_state(self, line):
        new_line = line.replace('\r','').replace('\n','')

        try:
            command,result = new_line.split(':', 1)
        except ValueError:
            debug.write(f"ValueError {line}\r\n")
            return

        if "CEREG" in command:
            result = result.split(',')
            try:
                if int(result[1]) in [1,5]:
                    with lock:
                        self.state = REGISTERED

            except ValueError:
                pass

        #+QCCID:
        elif "QCCID" in command:
            self.ccid = result.strip()

        # +QMTOPEN: <TCP_connectID>,<result>
        elif "QMTOPEN" in command:
            result = result.split(',')
            try:
                if int(result[1]) == 0:
                    self.state = MQTTOPEN
            except ValueError:
                pass

        # +QMTSTAT: 0,7
        elif "QMTSTAT" in command:
            result = result.split(',')
            if int(result[1]) > 0:
                self.state = MQTTOPEN

        # +QMTCONN: <TCP_connectID>,<result>[,<ret_code>]
        elif "QMTCONN" in command:
            result = result.split(',')
            if int(result[1]) == 0:
                self.state = MQTTCONNECTED

        # +QMTSTAT: <TCP_connectID>,<err_ code> 1,2,3
        elif "QMTSTAT" in command:
            pass

        #+QMTRECV: 0,0,"device/status","it works"
        elif "QMTRECV" in command:
            result = result.split(',')
            line = result[3]
            with lock:
                lines.append(line)

        elif "QNBIOT" in command:
            with lock:
                lines.append(result)

        elif "QCFG" in command:
            pass

    def send_at(self, command):
        debug.write(f"sending {command}\r\n")
        command += '\r'
        command = bytes(command, 'utf-8')
        try:
            modem.write(bytes(command))
        except Exception as e:
            debug.write("error: {e} writing {command}\r\n")

        while True:
            with lock:
                if lines:
                    line = lines.pop(0)
                    if 'OK' in line or 'ERROR' in line:
                        break

            time.sleep(1)

    def wait_for(self, on):
        while True:
            with lock:
                if lines:
                    line = lines.pop(0)
                    if on in line:
                        return line

            time.sleep(1)
            continue

    @property
    def state(self):
        return self.status

    @state.setter
    def state(self, value):
        self.status = value



def mqtt(bc66):
    global alarm_set

    bc66.send_at('AT+QMTOPEN=0,"broker.hivemq.com",1883')
    while not bc66.state == MQTTOPEN:
        utime.sleep(1)

    bc66.send_at('AT+QMTCONN=0,"petes-alfa-kit"')
    while not bc66.state == MQTTCONNECTED:
        utime.sleep(1)

    bc66.send_at('AT+QMTSUB=0,1,"device/status",0')
    msg = json.dumps({'ccid':bc66.ccid, 'alarm':alarm_set, 'timestamp':now()})

    bc66.send_at('AT+QMTPUB=0,0,0,0,"device/state","{}"'.format(msg))
    utime.sleep(1)
    bc66.send_at('AT+QMTCLOSE=0')


def main():
    debug.write("Ready\r\n")
    bc66 = BC66()
    bc66.wait_for("BROM")

    while True:
        #bc66.send_at("ATI")
        bc66.send_at("AT+CCLK?")
        bc66.send_at("AT+QCCID")
        bc66.reset()
        bc66.send_at('AT+CEREG=5')
        bc66.send_at('AT+QNBIOTEVENT=1,1')
        #bc66.send_at('AT+CPSMS=1,,,"10100100","00000100"')
        bc66.send_at('AT+CPSMS=1,,,"00100110","00100001"')
        bc66.send_at('AT+CEREG?')
        #bc66.send_at('AT+QSCLK?')
        #bc66.send_at('AT+CBC')

        mqtt(bc66)


        while True:
            time.sleep(1)

            with lock:
                if not lines:
                    continue

                line = lines.pop(0)
                debug.write(f"{now()}\r\n")
                try:
                    debug.write(f"{line}\r\n")
                except TypeError as e:
                    debug.write(f"{str(e)}")

            if "BROM" in line:
                break


if __name__ == "__main__":
    main()
