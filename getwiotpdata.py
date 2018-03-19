# *****************************************************************************
# Copyright (c) 2018 IBM Corporation and other Contributors.
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Eclipse Public License v1.0
# which accompanies this distribution, and is available at
# http://www.eclipse.org/legal/epl-v10.html
#
# Contributors:
#    Ranjan Dasgupta             - Initial drop for Alpha release
#
# *****************************************************************************

#
# Connector application used for inegrating Watson IoT Platform with QRadar
#

import os
import logging
import logging.handlers
from datetime import datetime
import time
import threading
from threading import Thread
from threading import Lock
import sys
import json
import re
import ibmiotf
import ibmiotf.application
import ConfigParser
import signal
import socket

# SYSLOG setup
# Application names - 
APPNAMECONNECTION = "wiotp_qradar:1.0:Connection "
# APPNAMEDEVICEMGMT = "wiotp_qradar:1.0:DevMgmt "
sysLogger = logging.getLogger('WIOTPSYSLOG')

# Setup Application logger to console
applogger = logging.getLogger('qradar-connector')
applogger.setLevel(logging.DEBUG)
conlogger = logging.StreamHandler()
conlogger.setLevel(logging.DEBUG)
applogger.addHandler(conlogger)

# Variables to control WIoTP API invocation
# 
# Variables used to control time period in GET /connection/logs API
# Time periods ar in ISO8601 format
curTime = time.gmtime()
lastTime = curTime
curISOTime = time.strftime("%Y-%m-%dT%H:%M:%S", curTime)
lastISOTime = curISOTime

# compile regular expressions
authREObj = re.compile(r'(.*): ClientID=\S(.*?)\S, ClientIP=(.*)')
connREObj = re.compile(r'^Closed\sconnection\sfrom\s(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\.(.*)')
genrREObj = re.compile(r'(.*)ClientIP=(.*)')

systemIP = '127.0.0.1'
test_mode = 0

# Signal handler
def signalHandler(sig, frame):
    applogger.info("Exit program on SIGINT")
    sys.exit(0)

#
# Get local IP address
def getLocalIP():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 1))
        IP = s.getsockname()[0]
    except:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

#
# Function to process device log messages and generate syslog events
#
def processLogEvent(clientId, log, verbose):
    global test_mode
    global systemIP

    # This function parses log event and generate syslog event.
    # Log event from WIoTP is in JSON format. Example of a typical log event:
    # {"timestamp": "2018-02-28T20:02:50.585Z", "message": "Token auth succeeded: ClientID='d:li0f0v:NXPDev:testSub', ClientIP=32.97.110.54"}

    # SYSLOG Event format:
    # <timestamp> <localip> <APPNAME>: devType=<devType> devId=<devId> Message=<Raw log message>

    timestamp = log["timestamp"]
    msg = log["message"]

    if test_mode == 1:
        cT = time.gmtime()
        tstamp = time.strftime("%b %d %H:%M:%S", cT)
        syslog_header = "%s %s " % (tstamp, systemIP)
    else:
        syslog_header = "%s %s " % (timestamp, systemIP)

    headMsg = syslog_header + APPNAMECONNECTION

    # Parse authentication messages
    mObj = authREObj.match(msg)
    if mObj:
        message = mObj.group(1)
        clientId = mObj.group(2)
        IP = mObj.group(3)
        event = "AuthSucceeded"
        if "failed" in message:
            event = "AuthFailed"
        eventMsg = "%s source=%s event=%s clientID=%s Message=%s" % (headMsg, IP, event, clientId, message)
        applogger.debug(eventMsg)
        sysLogger.info(eventMsg)
        return
        
    # Parse connection closed messages
    mObj = connREObj.match(msg)
    if mObj:
        message = mObj.group(2)
        IP = mObj.group(1)
        event = "ClosedNormal"
        if "by the client" in message:
            state = "ClosedByClient"
        if "not authorized" in message:
            event = "OperationUnauthorized"
        eventMsg = "%s source=%s event=%s clientID=%s Message=%s" % (headMsg, IP, event, clientId, message)
        applogger.debug(eventMsg)
        sysLogger.info(eventMsg)
        return
        
    # Process generic log
    # check if ClientIP is specified in message
    event = "NA"
    IP = "NA"
    mObj = genrREObj.match(msg)
    if mObj:
        IP = mObj.group(2)

    eventMsg = "%s source=%s event=%s clientID=%s Message=%s" % (headMsg, IP, event, clientId, msg)
    applogger.debug(eventMsg)
    sysLogger.info(eventMsg)


#
# Get all device data from Watson IoT Platform
#
def getDevices(client, device_limit, log_limit, verbose):

    applogger.info("Start a new pool cycle ...")
    _getPageOfDevices(client, device_limit, log_limit, verbose, None )

#
# Get device data in chunks
# 
def _getPageOfDevices(client, device_limit, log_limit, verbose, bookmark):

    deviceList = client.api.getDevices(parameters = {"_limit": device_limit, "_bookmark": bookmark, "_sort": "typeId,deviceId"})
    resultArray = deviceList['results']

    applogger.info("Process connection logs of " + str(len(resultArray)) + " devices")
    for device in resultArray:
        if "metadata" not in device:
            device["metadata"] = {}

        typeId = device["typeId"]
        deviceId = device["deviceId"]
        clientId = device["clientId"]

        applogger.debug("ClientID=" + clientId)

        try:
            # get logs for the device 
            if log_limit == 0:
                logresults = client.api.getConnectionLogs({"typeId":typeId, "deviceId":deviceId, "fromTime": lastISOTime, "toTime": curISOTime})
            else:
                if log_limit == -1:
                    logresults = client.api.getConnectionLogs({"typeId":typeId, "deviceId":deviceId})
                else:
                    logresults = client.api.getConnectionLogs({"typeId":typeId, "deviceId":deviceId, "_limit": log-fetch-limit})
 
  
            for log in logresults:
                processLogEvent(clientId, log, verbose)
                applogger.debug(json.dumps(log))

        except Exception as e:
            applogger.error(str(e))


    # Next page
    if "bookmark" in deviceList:
        bookmark = deviceList["bookmark"]
        _getPageOfDevices(client, device_limit, log_limit, verbose, bookmark)

#
# Get device data and log events
#
def getEventFromAPI(client, device_limit, log_limit, verbose):
    try:
        getDevices(client, device_limit, log_limit, verbose)

    except ibmiotf.APIException as e:
        applogger.error(e.httpCode)
        applogger.error(str(e))
        return
    except Exception as e:
        applogger.info(str(e))
        return

#
# Pooling function to perodically invoke REST API to get device logs and data from WIoTP
#
def getDataAndProcess(configData): 
    global test_mode
    cycle = 0
    loop = 0

    test_mode = configData['test_mode'];
    nloop = int(configData['cycles'])
    device_limit = int(configData['device_fetch_limit'])
    log_limit = int(configData['log_fetch_limit'])
    interval = int(configData['log_fetch_interval'])
    verbose = configData['verbose']

    # Set current time in ISO8601 - needed for log fetch API
    curTime = time.gmtime()
    curISOTime = time.strftime("%Y-%m-%dT%H:%M:%S", curTime)
    applogger.info("Current time: " + curISOTime + "\n")

    # Get API client
    config = "application.cfg"
    client = None
    options = ibmiotf.application.ParseConfigFile(config)
    try:
        client = ibmiotf.application.Client(options)
        client.logger.setLevel(logging.INFO)

    except Exception as e:
        applogger.error(str(e))
        return

    while True:
        loop += 1
        applogger.debug("Get Cycle: Loop [{0}] of [{1}]".format(str(loop),str(nloop)))
    
        # set current time
        curTime = time.gmtime()
        curISOTime = time.strftime("%Y-%m-%dT%H:%M:%S", curTime)

        getEventFromAPI(client,device_limit,log_limit,verbose)

        # set last time
        lastISOTime = curISOTime

        # check for test cycle
        if nloop > 0 and loop == nloop:
            break

        time.sleep(int(interval))

    applogger.info("STOP Loop \n")


# Configure syslog server and spawn thread to get connection logs from WIoTP and generate 
# syslog events
def get_wiotp_data():
    global sysLogger
    global systemIP

    # Set up signal handler
    signal.signal(signal.SIGINT, signalHandler)

    applogger.info("Start qradar-connector")

    # Read configuration file to read qradar syslog server host IP and Port
    cwd = os.getcwd()
    configpath = cwd + "/application.cfg"

    # Get configuration data
    config = ConfigParser.ConfigParser()
    config.read(configpath)

    # SYSLOG server address and port
    syslog_server_address = config.get("qradar-syslog-server", "hostip")
    syslog_server_port = config.getint("qradar-syslog-server", "port")

    applogger.info("syslog_server_address: " + syslog_server_address )
    applogger.info("syslog_server_port: " + str(syslog_server_port) )

    # read parameters used for invoking WIoTP API calls and processing data
    configData = {}

    # Check for test mode
    configData['test_mode'] = config.getint("qradar-connector", "test-mode")

    # Set number of cycles - default is 0 (loop for ever)
    configData['cycles'] = config.getint("qradar-connector", "cycles")

    # Chunk limit for getting device data
    configData['device_fetch_limit'] = config.getint("qradar-connector", "device-fetch-limit")

    # Log fetch strategy
    # 0 (use time period), 1 (use limit), -1 (get all)
    configData['log_fetch_limit'] = config.getint("qradar-connector", "log-fetch-limit")

    # Log fetch pooling interval in seconds
    configData['log_fetch_interval'] = config.getint("qradar-connector", "log-fetch-interval")

    # verbose mode - default True
    configData['verbose'] = config.getint("qradar-connector", "verbose")
   
    # Log Level - default INFO
    configData['level'] = config.get("qradar-connector", "level")

    systemIP = getLocalIP()

    # Set log level
    applogger.removeHandler(conlogger)
    conlogger.setLevel(configData['level'])
    applogger.addHandler(conlogger)

    applogger.debug("Configuration Data:")
    applogger.debug(json.dumps(configData, indent=4))

    # Set Syslog handler
    sysLogger.setLevel(logging.INFO)
    syslog_handler = logging.handlers.SysLogHandler( address=(syslog_server_address, syslog_server_port), facility=logging.handlers.SysLogHandler.LOG_LOCAL1)
    sysLogger.addHandler(syslog_handler)

    getDataAndProcess(configData)

 
if __name__ == '__main__':
    get_wiotp_data()

