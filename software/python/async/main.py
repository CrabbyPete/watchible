import re
import time
import uasyncio as asyncio

from bc66 import MQTTClient, PSM_SLEEP, logfile

log = logfile

def on_subscribe(msg):
    log(f"Recieved:{msg}")


def on_connect(*result):
    log(f"Connected:{result}")


def on_disconnect(*result):
    log(f"Disconnect:{result}")


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
        rtc.datetime(values)

    t = time.localtime()
    try:
        s = f"{t[0]}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d}"
    except TypeError as e:
        return str(e)
    return s


async def main(client):
    """
    Main loop. This should run forever.
    :param client: MQTT client from bc66
    :return: Never
    """
    
    task = asyncio.create_task(client.reader())		# Start reading from the modem
    await client.reset() 							# Reset the modem so we are in a known space   
    await client.ssl()								# Set up the AWS certs

    while True:
        await client.network_ready()
        commands = [
            'qsclk=0',  # Turn off PSM while we send commands
            'qledmode=0',  # Turn off LED on network
            'cedrxs=0',  # Turn off DRX
            'qnbiotevent=1,1',  # Report PSM events
            # 'cpsms=1,,,"00101100","00000001"',    # PSM Mode. Wake for 2 seconds, sleep 12 hours 1, 6 hours
            f'qpsms=0,{PSM_SLEEP}',
            'cclk?',  # Get the time
            'qccid',  # Get the ccid
            'cbc',  # Get the battery level
            'qpsms?'
        ]

        for command in commands:
            client.at(command)
            await asyncio.sleep_ms(10)

        if await client.open():
            if await client.connect():  # Connect to the MQTT server
                # await client.subscribe('device/update')        # Set up the subscription
                message = await client.report()
            await client.publish('device/update', message)

        await client.close()

        now = time.time() + PSM_SLEEP
        hours = 1

        client.at('qsclk=1')   # Turn on PSM
        while True:
            if client.psm:
                break
            await asyncio.sleep_ms(100)


        while client.psm:
            log("sleep @{}".format(time_str()))
            # time.sleep(60*60)
            machine.lightsleep(3600000)  # Sleep for an hour at a clip an interrupt will cause it to wake
            if client.alarm_set():
                log('alarm triggered')
                break

        log("wake up @{}".format(time_str()))


config = {'on_subscribe' : on_subscribe,
          'on_connect'   : on_connect,
          'on_disconnect': on_disconnect}

client = MQTTClient(config)

try:
    asyncio.run(main(client))
except Exception as e:
    print(f"Exception occured in main:{e}")
finally:
    client.close()

