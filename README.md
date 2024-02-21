# Watchible: Cellular NB-IOT Board For Raspberry Pi Pico

Watchible is an add-on board for the Raspberry Pi Pico that connects to the internet via low cost cellular 
NB-IOT.  The advantage of NB-IOT is that it is very low cost, and and can be very low power. 
It is meant to monitor any trigger with a low impedance interface. This is useful if in an applications
such as water alarm detector, where there is no WIFI available, or monitoring an alarm in a remote location or field. 

![Watchible boards ](http:./img.png)

### Features:

1. ##### Global connectivity
   NB-IoT is a cellular low-power wide-area network (LPWAN) technology standard. It’s deployed over on existing cellular LTE networks.
   NB-IoT can be the best solution for connecting people and businesses to devices that transact small data amounts in a fixed location, and uses standard protocols
   such as MQTT, CoAP, and HTTP. The Watchible board uses all bands is able to work globally, and many of the
   major providers supply SIM cards and very low cost plans

2. ##### Programability
   Because the Watchible board sits on the Raspberry Pi Pico It can easily be programmed to use any cloud IOT service, such as AWS, Azure,
   Google, or HiveMQTT. It can be programmed in C/C++ and Python. When placed in a hardened enclosure Watchible provides a
   small profile remote monitor that can be placed anywhere and simply run with little to no maintenance.

   With the addition of other Pico add-on boards the number of things that can be monitored is limitless. Examples are remote water detection on a moored boat.
   Ultasonic fault detection on remote machinery, ice detection on bridges. and many more. The Watchible boards has an on-board I2C connector, so any number 
   of sensors can be easily be added, and monitored. 

3. ##### Low power 
   Both the Pico and the Quectel BCC-66 modem can be put to sleep for up to 12 hours, and will wake up if an alarm is triggered.
   This greatly reduces power consumption and allows Watchible to be placed in remote locations, powered by a simple battery for months or
   years, depending on how big the battery is and how many times it has to wake up to insure a connection to the network. With other add on boards the number of things that can be monitored is limitless. Examples are remote water detection,
   gas sensors, and any kind of switch.   

4. ##### Alternative to LORA
   Watchible is an alternative to LoRa networks which may require gateway build out. Each device is independent
   and has no single point of failure should the gateway fail.  
   While it is not specified in NB-IOT applications, I have also tested it on a moving vehicle, and it worked.

5. ##### Low cost 
   The cost of the Watchible board is very low due to the low cost of the Quectel BC66 family of modem, which are specifically
   designed for NB-IOT. Cellular plans are available that are as low $10.00 for 10 years. 






