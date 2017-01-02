# arpscanner
This is a plugin for the pygate gateway. It provides a mechanism for detecting the presence of devices on the network.
In other words, it's a presence detection tool

# How it works
The system regularly performs an arp-scan to detect known devices on the network. To determine which devices need to be tracked, the user has to specify the mac address of the device. This can be done after installation, through the AllThingsTalk cloud interface: just set the json list of all the mac addresses that need to be tracked.

# installation

- Make certain that [pygate](https://github.com/allthingstalk/pygate) and all of it's dependencies have been installed first.
- download the module
- install the module: copy the directory pygate_arpscanner to the root directory of the pygate software (at the same level as pygate.py)  
- install arp-scan: run the command `sudo apt-get install arp-scan`

# activate the plugin
the plugin must be activated in the pygate software before it can be used. This can be done manually or through the pygate interface.

## manually
Edit the configuration file 'pygate.conf' (located in the config dir).
add 'virtualdevices' to the config line 'modules' in the general section of the 'pygate.conf' config file. ex:  
    
	[general]  
    modules = arpscanner; watchdog
When done, restart the gateway.
 
## pygate interface
Use the actuator 'plugins' and add 'arpscanner' to the list. After the command has been sent to the device, the gateway will reboot automatically.

# configuration

- click on the 'refresh visible devices' button in the UI to get a list of available mac addresses
- for each device that you want to track, copy the mac address and put it in the list of 'devices being tracked', like so: ["xxxx", "xxxx"]

# limitations

- The module currently only works on eth0 port, you can change this in _init_.pi
- the module scans every second, which is hardcoded. If you want to change this, change the delay in _init_.py or add an actuator that controls the delay.  
