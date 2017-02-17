__author__ = 'Jan Bogaerts'
__copyright__ = "Copyright 2016, AllThingsTalk"
__credits__ = []
__maintainer__ = "Jan Bogaerts"
__email__ = "jb@allthingstalk.com"
__status__ = "Prototype"  # "Development", or "Production"

import logging
logger = logging.getLogger('arpscanner')
import os,subprocess
from time import sleep
import json
from threading import Lock, Thread, Event
import datetime
import ping

from pygate_core import config, cloud , device, modules


_device = None
_tracked_devices = {}                # dict of devices that need to be tracked. each device is an object, cause we need to store state info locally.
_tracked_devices_lock = Lock()
_isRunning = True
_arp_command = None
_pinger = None                      # maintains a ref to the thread that performs the pinging.
_min_departure_count = None         # the minimum count that a device has to be seen as gone before labeling it as such (to prevent wobbles when high sampling frequencies are used
_refresh_frequency = None           # the rate at which the data is refreshed, in seconds.

_pinger_wake_up_event = Event()
_main_wake_up_event = Event()


class Tracked:
    def __init__(self, name):
        # note: we dnon't need to store the value, this is stored in the assetStateCache.
        self.name = name
        self.ip = None                      # the ip address for this device being tracked.
        self.changeCount = 0                # sometimes a device disapears 1 cycle, but it's still there, so we compensate

class Pinger(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.isRunning = True

    def run(self):
        while self.isRunning:
            try:
                start = datetime.datetime.now()
                foundDevices = {}
                _tracked_devices_lock.acquire()
                try:
                    devs = dict(_tracked_devices)       # make a local copy of the dict, so the list can be modified by the other thread.
                finally:
                    _tracked_devices_lock.release()
                for key, dev in devs:
                    if dev.ip and ping.do_one(dev.ip, 500):            #timeout as fast as possible. if the dev has no ip, it is not on the network (or not yet seen, so don't ping).
                        foundDevices[dev.mac] = dev.ip
                updateAssetStates(foundDevices)
                refresh_every = datetime.timedelta(seconds=_refresh_frequency)
                time_dif = (datetime.datetime.now() - start)
                if time_dif < refresh_every:
                    time_dif = refresh_every - time_dif
                    _pinger_wake_up_event.wait(time_dif.total_seconds())        #wake up if the service stops.
                else:
                    logger.error("pinger time overrun: pinging took longer than refresh rate")
            except:
                logger.exception("ping thread failed")


VISIBLE_DEV_ID = "visibledev"           # id for assets
REFRESH_VISIBLE_DEV_ID = "refreshvisibledev"
TRACKED_DEV_ID = "trackeddev"
ARP_COMMAND_ID = "arpcommand"
USE_PING_ID = "useping"
DEV_ID = 'arpscanner'
MIN_DEPARTURE_CNT_ID = "mindeparturecount"
REFRESH_FREQ_ID = "refreshfrequency"

def connectToGateway(moduleName):
    '''optional
        called when the system connects to the cloud.'''
    global _device
    _device = device.Device(moduleName, DEV_ID)


def loadAssets(list):
    """
    load the objects that need to be checked by the arp scanner.
    :param list: the list of mac addresses to scan for
    :return: None
    """
    if list:
        _tracked_devices_lock.acquire()
        try:
            for item in list:
                name = str(item.replace(':', ''))        # remove unwanted signes from the label, so we can use it as name for the asset
                _tracked_devices[str(item)] = Tracked(name)
        finally:
            _tracked_devices_lock.release()



def syncAssets(new, current):
    """
    sync the assets. Doesn't remove assets, only adds (some assets might be from external components)
    :type current: list
    :type new: list
    :param new: the list that contains the names of the assets that should be loaded
    :param current: the list of asset names currently defined in the system.
    :return:  None
    """
    _tracked_devices_lock.acquire()
    try:
        for item in new:
            name = str(item.replace(':', ''))
            item = str(item)
            if not item in current:
                _device.addAsset(name, item, "presence of device", "sensor", "boolean")
            if not item in _tracked_devices:
                _tracked_devices[item] = Tracked(name)
    finally:
        _tracked_devices_lock.release()
    # don't delete any

def syncDevices(existing, full):
    '''optional
       allows a module to synchronize it's device list.
       existing: the list of devices that are already known in the cloud for this module.'''
    global _arp_command, _min_departure_count, _refresh_frequency
    if not existing:
        _device.createDevice('arp scanner', 'keep track of the connectivity state for known devices')
    else:
        _arp_command = _device.getValue(ARP_COMMAND_ID)
        _min_departure_count = _device.getValue(MIN_DEPARTURE_CNT_ID)
        _refresh_frequency = _device.getValue(REFRESH_FREQ_ID)
        if(_device.getValue(USE_PING_ID) == True):
            start_ping()
    if not existing or full:
        _device.addAsset(VISIBLE_DEV_ID, 'visible devices', 'The list of all visibile devices', False, 'object')
        _device.addAsset(ARP_COMMAND_ID, 'arp command', 'the command used for performing the arp scan', 'virtual', 'string')
        _device.addAsset(USE_PING_ID, 'use ping', 'When true, departures will be detected using ping, which requires more resources but can be required for some routers', 'virtual','boolean')
        _device.addAsset(MIN_DEPARTURE_CNT_ID, 'min departure cnt', 'the minimum count that a device has to be seen as gone before labeling it as such - to prevent wobbles when high sampling frequencies are used', 'virtual','integer')
        _device.addAsset(REFRESH_FREQ_ID, 'refresh frequency', 'The rate at which the system tries to refresh the data, in seconds.', 'virtual','integer')
        _device.addAsset(REFRESH_VISIBLE_DEV_ID, 'refresh visible devices', 'Refresh the list of all visibile devices',True, 'boolean')
        _device.addAsset(TRACKED_DEV_ID, 'devices being tracked', 'The list of all devices that need to be tracked. Each device becomes an asset', True, '{"type": "array", "items":{"type":"string"}}')
    if full:                        # when not existing yet, no need to sync assets, there are no extra assets yet.
        if existing and 'assets' in existing:
            syncAssets(_device.getValue(TRACKED_DEV_ID), existing['assets'])
        else:
            syncAssets(_device.getValue(TRACKED_DEV_ID), [])
    else:
        loadAssets(_device.getValue(TRACKED_DEV_ID))                                        # alwaye need to load these, otherwise there is no mapping loaded in memory
    if not _arp_command:                                                                    # we check at the end, this way, we set a default value right from the first time.
        if os.name == 'nt':
            _arp_command = 'arp -a'
        else:
            _arp_command = 'sudo arp-scan -l -q'
        _device.send(_arp_command, ARP_COMMAND_ID)                                          # update the platform so that there is a default value.
    if not _min_departure_count:                                                            # set default value
        _min_departure_count = 2
        _device.send(_min_departure_count, MIN_DEPARTURE_CNT_ID)
    if not _refresh_frequency:
        _refresh_frequency = 1
        _device.send(_refresh_frequency, REFRESH_FREQ_ID)


def start_ping():
    """
    starts the ping thread that checks for devices that leave the network. If the user did not activate this feature,
    departures can also be detected by the arp-scan.
    :return: None.
    """
    global _pinger
    if not _pinger:
        _pinger = Pinger()
        _pinger.start()

def stop_ping():
    """stops the ping thread """
    global _pinger
    if _pinger:
        _pinger.isRunning = False
        _pinger = None
        _pinger_wake_up_event.set()

def findDevices():
    foundDevices = {}
    # Execute arp command to find all currently known devices
    proc = subprocess.Popen(_arp_command, shell=True, stdout=subprocess.PIPE)
    # Build array of dictionary entries for all devices found
    if os.name == 'nt':
        for line in proc.stdout:
            item = line.split()
            if len(item) == 3 and item[2] == 'dynamic':
                foundDevices[item[1].replace('-', ':').lower()] = item[0]
    else:
        lines = []
        for line in proc.stdout:  # skip the first 2 and last 3 lines
            lines.append(line)
        for line in lines[2:-3]:
            item = line.split()
            foundDevices[item[1].lower()] = item[0]
    # Wait for subprocess to exit
    proc.wait()
    return foundDevices


def updateAssetStates(current):
    """
    updates the list
    :param current: The new state, that was just discovered
    :return:
    """
    _tracked_devices_lock.acquire()
    try:
        for knownMac, knownName in _tracked_devices.iteritems():
            if knownMac in current:
                prevVal = _device.getValue(knownName.name)
                if not prevVal or prevVal == False:
                    logger.info('joined: ' + knownName.name)
                    _device.send('true', knownName.name)
                    knownName.ip = current[knownMac]            # store the ip address so we can ping it if need be
                else:
                    knownName.changeCount = 0
            elif _device.getValue(knownName.name) == True:
                knownName.changeCount += 1
                if knownName.changeCount > _min_departure_count:  # compensate: the device has to disapear for 2 cycles before we really report it gone.
                    logger.info('left: ' + knownName.name)
                    _device.send('false', knownName.name)
                    knownName.changeCount = 0
                    knownName.ip = None
            else:
                knownName.changeCount = 0
    finally:
        _tracked_devices_lock.release()

def run():
    ''' optional
        main function of the plugin module'''
    start = datetime.datetime.now()
    foundDevices = findDevices()
    _device.send(foundDevices, VISIBLE_DEV_ID)      # init state for the visible devices.
    while _isRunning:
        try:
            updateAssetStates(foundDevices)
            refresh_every = datetime.timedelta(seconds=_refresh_frequency)
            time_dif = (datetime.datetime.now() - start)
            if time_dif < refresh_every:
                time_dif = refresh_every - time_dif
                _main_wake_up_event.wait(time_dif.total_seconds())  # wake up if the service stops.
            else:
                logger.error("arp-scan time overrun: scan took longer than refresh rate")
            if _isRunning:
                start = datetime.datetime.now()
                foundDevices = findDevices()
        except Exception as e:
            logger.exception("failed to perform arp scan")


def stop():
    """ optional
        called when the application is stopped. Perform all the necessary cleanup here"""
    global _isRunning
    logger.info("stopping arp scanner")
    _isRunning = False
    _main_wake_up_event.set()
    stop_ping()


#callback: handles values sent from the cloudapp to the device
def onActuate(id, value):
    if id == TRACKED_DEV_ID:
        list = json.loads(value)
        _tracked_devices_lock.acquire()
        try:
            keys = _tracked_devices.keys()
        finally:
            _tracked_devices_lock.release()
        syncAssets(list, keys)       # the keys represent the existing devices, cause they have already been loaded.
        _device.send(list, id)
    elif id == REFRESH_VISIBLE_DEV_ID:
        foundDevices = findDevices()
        _device.send(foundDevices, VISIBLE_DEV_ID)
    elif id == ARP_COMMAND_ID:
        global _arp_command
        _arp_command = value
        _device.send(value, ARP_COMMAND_ID)
    elif id == USE_PING_ID:
        if bool(value) == True:
            start_ping()
        else:
            stop_ping()
    elif id == MIN_DEPARTURE_CNT_ID:
        global _min_departure_count
        _min_departure_count = int(value)
    elif id == REFRESH_FREQ_ID:
        global _refresh_frequency
        _refresh_frequency = int(value)
    else:
        print("unknown actuator: " + id)

