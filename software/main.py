import time

from watchible import BC66, time_str, RESET

def main():
    print(f"Ready:{time_str()}")

    bc66 = BC66()
    bc66.power_reset()
    
    while True:
        # bc66.send_at("ATI")
        bc66.send_at("AT+CCLK?")           # Get the time
        bc66.send_at("AT+QCCID")           # Get the CCID
        bc66.send_at('AT+CEREG=5')         # Tell network to enable PSM
        bc66.send_at('AT+QNBIOTEVENT=1,1') # Enable event reporting

        bc66.send_at('AT+CPSMS=1,,,"00100001","00100001"')  # Set PSM Mode 1 hour sleep, 1 minute run
        bc66.send_at('AT+CEREG?')
        bc66.send_at('AT+QSCLK?')          # Enable light and deepsleep PSM
        bc66.send_at('AT+CBC')             # Battery level
        bc66.send_at('AT+CGDCONT?')
        while not bc66.ip:
            time.sleep(.5)

        bc66.mqtt()

        while not bc66.state == RESET:
            time.sleep(2)


if __name__ == "__main__":
    main()

