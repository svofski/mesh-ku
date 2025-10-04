#!/usr/bin/env python3
import os
from util import *
import logging
import session

DEFAULT_BLOCK_SIZE=128
MAX_RESEND_COUNT=333
MESHKU_VERSION_STR="V001"
HELLO_PREFIX="KU!"

# 0.25s schedulerticks
TICK_TIME=0.25

SEND_START_INTERVAL_TICKS=15//TICK_TIME
STATUS_INTERVAL_TICKS=10//TICK_TIME
BODY_INTERVAL_TICKS=3//TICK_TIME
BODY_INTERVAL_SPREAD=3
# 1: 17.0cps ratio 4.5
# 2: 19.8cps ratio 2.38
# 3: 20.0cps ratio 1.62
# 4: 20.9cps ratio 1.25
# 5: 15.4cps ratio 1.38

# longer test (ADSKOK)
# 4: 19.3 ratio 1.59 10m24s sender 10m18s receiver
# 4 +/-3 randomizer: ratio: 1.33 elapsed: 9m 2s cps: 22.2
#   
# IN FOIL 4 +/-3
# Sending ADSKOK.ROM complete; blocks: 94 sent: 215 ratio: 2.29 elapsed: 14m 56s cps: 13.4
# NO FOIL 4 +/-3
# Sending ADSKOK.ROM complete; blocks: 94 sent: 130 ratio: 1.38 elapsed: 9m 4s cps: 22.1
# NO FOIL 3 +/-3 blocks: 94 sent: 170 ratio: 1.81 elapsed: 9m 1s cps: 22.2 (but receiver shows cps 24.9)

# meshtastic port numbers 
TEXT_MESSAGE_APP = 1
PRIVATE_APP = 256 

# our port numbers
HELLO_APP=TEXT_MESSAGE_APP
DATA_APP=PRIVATE_APP+44

def setup_logger(role):
    logger = logging.getLogger("mesh-ku-" + role)
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh1 = logging.FileHandler(role + "-events.log")
    fh1.setLevel(logging.INFO)
    fh1.setFormatter(fmt)
    logger.addHandler(fh1)

    fh2 = logging.FileHandler(role + "-debug.log")
    fh2.setLevel(logging.DEBUG)
    fh2.setFormatter(fmt)
    logger.addHandler(fh2)

    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger

if __name__ == '__main__':
    hostname = short_hostname()
    session.logger = setup_logger(hostname)
    scheduler = session.Scheduler(hostname)

    try:
        mode = sys.argv[1]
        if mode == "send":
            did = sys.argv[2]
            files = sys.argv[3:]
            dstId, longId = get_destinationId(scheduler.iface, did)
            if dstId == None:
                session.logger.warning(f"Could not find nodedb entry for [{did}]")
                exit(1)
            session.logger.warning(f"Resolved {did} as {dstId} {longId}")
    except Exception as e:
        print(e)
        print("Usage: \n\tmeshku send recipient-id file1 file2... \n\tmeshku receive")
        exit(1)

    if mode == "send":
        for f in files:
            scheduler.send_file(f, dstId)

    session.logger.debug("Created scheduler")
    while True:
        scheduler.sched()
        time.sleep(TICK_TIME)
