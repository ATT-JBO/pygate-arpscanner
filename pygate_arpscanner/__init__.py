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
from threading import Lock

from pygate_core import config, cloud , device, modules

class Tracked:
    def __init__(self, name):
        # note: we dnon't need to store the value, this is stored in the assetStateCache.
        self.name = name
        self.changeCount = 0                # sometimes a device disapears 1 cycle, but it's still there, so we compensate

_device = None
_tracked_devices = {}                # dict of devices that need to be tracked. each device is an object, cause we need to store state info locally.
_tracked_devices_lock = Lock()
_isRunning = True

VISIBLE_DEV_ID = "visibledev"           # id for assets
REFRESH_VISIBLE_DEV_ID = "refreshvisibledev"
TRACKED_DEV_ID = "trackeddev"
DEV_ID = 'arpscanner'

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
    if not existing:
        _device.createDevice('arp scanner', 'keep track of the connectivity state for known devices')
    if not existing or full:
        _device.addAsset(VISIBLE_DEV_ID, 'visible devices', 'The list of all visibile devices', False, 'object')
        _device.addAsset(REFRESH_VISIBLE_DEV_ID, 'refresh visible devices', 'Refresh the list of all visibile devices', True, 'boolean')
        _device.addAsset(TRACKED_DEV_ID, 'devices being tracked', 'The list of all devices that need to be tracked. Each device becomes an asset', True, '{"type": "array", "items":{"type":"string"}}')
    if full:                        # when not existing yet, no nee to sync assets, there are no extra assets yet.
        if existing and 'assets' in existing:
            syncAssets(_device.getValue(TRACKED_DEV_ID), existing['assets'])
        else:
            syncAssets(_device.getValue(TRACKED_DEV_ID), [])
    else:
        loadAssets(_device.getValue(TRACKED_DEV_ID))                                        # alwaye need to load these, otherwise there is no mapping loaded in memory


def findDevices():
    foundDevices = {}
    # Execute arp command to find all currently known devices
    if os.name == 'nt':
        proc = subprocess.Popen(config.getConfig("arpscanner", "arp command", 'arp -a'), shell=True, stdout=subprocess.PIPE)
    else:
        proc = subprocess.Popen(config.getConfig("arpscanner", "arp command", 'sudo arp-scan -l -q'), shell=True, stdout=subprocess.PIPE)
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
    :param current:
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
                else:
                    knownName.changeCount = 0
            elif _device.getValue(knownName.name) == True:
                knownName.changeCount += 1
                if knownName.changeCount > int(config.getConfig("arpscanner", "min departure scan count", '2')):  # compensate: the device has to disapear for 2 cycles before we really report it gone.
                    logger.info('left: ' + knownName.name)
                    _device.send('false', knownName.name)
                    knownName.changeCount = 0
            else:
                knownName.changeCount = 0
    finally:
        _tracked_devices_lock.release()

def run():
    ''' optional
        main function of the plugin module'''
    foundDevices = findDevices()
    _device.send(foundDevices, VISIBLE_DEV_ID)      # init state for the visible devices.
    while _isRunning:
        try:
            updateAssetStates(foundDevices)
            sleep(1)
            if _isRunning:
                foundDevices = findDevices()
        except Exception as e:
            logger.exception("failed to perform arp scan")


def stop():
    """ optional
        called when the application is stopped. Perform all the necessary cleanup here"""
    global _isRunning
    logger.info("stopping arp scanner")
    _isRunning = False


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
    else:
        print("unknown actuator: " + id)