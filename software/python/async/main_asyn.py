
import uasyncio as asyncio

from bc66 import MQTTClient

connected = False

def on_subscribe(msg):
    print(f"Recieved:{msg}")


def on_connect(*result):
    print(f"Connected:{result}")


def on_disconnect(*result):
    print(f"Disconnect:{result}")
    connected = False


async def main(client):
    """
    Main loop. This should run forever.
    :param client: MQTT client from bc66
    :return: Never
    """
    global connected
    task = asyncio.create_task(client.reader())		# Start reading from the modem			
    await client.reset() 							# Reset the modem so we are in a known space

    await client.network()							# Connect to the cellular network
    await client.ssl()								# Set up the AWS certs
    await client.connect()							# Connect to the MQTT server
    await client.subscribe('device/update')         # Set up the subscription

    while True:
        await asyncio.sleep(30)
        message = await client.report()
        await client.publish('device/update', message)


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

