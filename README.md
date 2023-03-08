# watchible
Watchible NB-IOT board

Watchible is an NB-IOT add-on board for the Raspberry Pi Pico. 
It is low cost, and low power. It is meant to monitor any trigger with a low impedance interface. 
Both the Pico and the Quectel BCC-66 modem can be put to sleep for up to 12 hours, and will wake up if an alarm is triggered. 
This reduces power consumption and allows Watchible to be in remote locations, powered by a simple battery for months or 
years, depending on how big the battery is and how many times it has tow wake up to insure a connection to the network.  
With other add on boards the number of things that can be monitored is limitless. Examples are remote water detection, 
gas sensors, and any kind of switch.  The board can be used in fault analysis in remote places like buildings or bridges, 
using ultrasonic or vibration analysis.

Because it sits on the Raspberry Pi Pico It can easily be programmed to use any cloud IOT service, such as AWS, Azure, 
Google, or HiveMQTT. It can be programmed in C/C++ and Python. When placed in a hardened enclosure Watchible provides a 
small profile remote monitor that can be placed anywhere and simply run with little to no maintenance.

Watchible is an alternative to LoRa networks which require gateway build out and testing and much more maintenance, 
and have a single point of failure should the gateway fail.  
While it is not specified in NB-IOT applications, I have also tested it on a moving vehicle, and it worked. 


