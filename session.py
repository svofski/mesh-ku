import os, sys
import zlib
from pathlib import Path
import time
import secrets
import hashlib
import math
import struct
import random

import meshtastic
import meshtastic.serial_interface
import meshtastic.tcp_interface
from pubsub import pub

from meshku import *

def create_unique_session_id():
    """Generates a unique session ID using timestamp, random salt, and hashing."""
    # 1. Get the current high-resolution timestamp
    timestamp = str(time.time())

    # 2. Generate a cryptographically secure random salt
    # A 32-byte salt is very secure and is a standard size for many algorithms.
    salt = secrets.token_bytes(32)

    # 3. Concatenate the timestamp and salt, then hash them
    # Use a strong hashing algorithm like SHA-256
    id_data = timestamp.encode('utf-8') + salt
    session_id_hash = hashlib.sha256(id_data).hexdigest()

    return session_id_hash


def get_timestamp(path):
    mod_time_float = os.path.getmtime(path)
    mod_time_int = int(mod_time_float)
    return str(mod_time_int)

def chunk_bytearray(data, chunk_size):
    """Yields chunks of a bytearray of a specified size."""
    for i in range(0, len(data), chunk_size):
        yield data[i:i + chunk_size]

STATE_INITIAL = 0
STATE_INITIATE_SEND = 2
STATE_SEND = 3

STATE_RECEIVE = 10

STATE_FINISHED = 100

STATE_ABORTED = 255

class Session:
    def __init__(self, file_path = None, session_id = None, block_size = DEFAULT_BLOCK_SIZE):
        self.session_id = session_id
        self.file_path = file_path
        self.file_timestamp = 0
        self.block_size = block_size
        self.blocks = []
        self.state = STATE_INITIAL
        self.receiving = False
        self.finished = False
        self.iface = None
        self.destinationId = None
        self.crc32 = 0
        self.sender_version = "unknown"
        self.send_map = ""

    def abort(self):
        if self.state == STATE_RECEIVE:
            self.state = STATE_ABORTED

    def prepare_to_send(self):
        if self.session_id == None:
            self.session_id = create_unique_session_id()[-8::]

        self.file_timestamp = get_timestamp(self.file_path)

        content = None
        with open(self.file_path, "rb") as file:
            content = bytearray(file.read())
            self.file_size = len(content)
            self.crc32 = zlib.crc32(content)

        if content != None:
            self.blocks = list(chunk_bytearray(content, self.block_size))

        self.send_map = "." * len(self.blocks)
        
    def make_send_request(self):
        file_name = Path(self.file_path).name
        return f"{HELLO_PREFIX} {file_name} {self.file_timestamp} {self.file_size} {self.block_size} {self.crc32} {MESHKU_VERSION_STR} {self.session_id}"

    def parse_initial_packet(self, text) -> bool:
        print(f"parse_initial_packet: text=[{text}]")
        try:
            parts = text.split(" ")
            print(f"parse_initial_packet: parts={repr(parts)}")

            if parts[0] != HELLO_PREFIX:
                return False

            self.file_path = parts[1]
            self.file_timestamp = int(parts[2])
            self.file_size = int(parts[3])
            self.block_size = int(parts[4])
            self.crc32 = int(parts[5])
            self.sender_version = parts[6]
            self.session_id = parts[7]

            print(str(self))

            return True
        except Fuu:
            return False

    def __str__(self):
        return f"Session: file_path={self.file_path} file_timestamp={self.file_timestamp} file_size={self.file_size} " + \
                f"block_size: {self.block_size} crc32: {self.crc32} sender_version={self.sender_version} session_id={self.session_id}"

    def init_blocks(self):
        nblocks = math.ceil(self.file_size / self.block_size)
        self.blocks = [[]] * nblocks

    # convert blocks map into bitmap for status report,  0 = need, 1 = received
    def make_status_map(self):
        filemap = []
        byte = 0
        for n in range(len(self.blocks)):
            yesno = int(len(self.blocks[n]) > 0)
            byte = (byte << 1) | yesno
            if (n + 1) % 8 == 0:
                filemap.append(byte)
                byte = 0
        return filemap

    # bytes from the status packet -> 1 number per block: 0 = missing, 1 = received
    def decode_status_map(self, bytes):
        filemap = []
        for n in len(bytes):
            b = bytes[n]
            for i in range(8):
                yesno = int((b & 0x80) != 0)
                filemap.append(yesno)
                b <<= 1
        return filemap


    def start_receive(self, iface, senderId):
        self.iface = iface
        self.destinationId = senderId
        self.state = STATE_RECEIVE
        self.delay = 0
        self.init_blocks()


    def start_send(self, iface, destinationId, path):
        self.iface = iface
        self.destinationId = destinationId
        self.state = STATE_INITIATE_SEND
        self.delay = 0
        self.prepare_to_send()

    # data is raw packet data with session_id stripped
    def packet_received(self, data):
        if self.state == STATE_INITIATE_SEND or self.state == STATE_SEND:
            self.status_packet_received(data)
        elif self.state == STATE_RECEIVE:
            self.data_packet_received(data)

    def pick_at_random(self, s):
        candidates = [i for i in range(len(s)) if s[i] in {'>', '.'}]
        if not candidates:
            return -1
        return random.choice(candidates)

    def send_data_packet(self):
        # pick a block to send
        pick = self.pick_at_random(self.send_map) 
        if pick == -1:
            self.state = STATE_FINISHED
            printf(f"Sending {self.file_path} complete")
            return False
        self.send_map[pick] = ">"
        
        data = make_data_block(pick)
        self.iface.sendData(data, destinationId=self.destinationId, portNum=DATA_APP, wantAck=False)
        print(f"send_data_packet: sent block #{pick}")


    # uint16_t block-num
    # uint8_t byte-count
    # uint8_t array[byte-count]
    # uint32_t crc32
    def data_packet_received(self, data):
        block_num, block_len = struct.unpack_from('<HB', data, 0)
        data_start, data_end = 3, 3 + block_len
        block_data = data[3: 3 + block_len]
        crc_start = data_end
        if len(data) != crc_start + 4:
            raise ValueError(f"Buffer size mismatch: block #{block_num}/{block_len}bytes need: {crc_start+4} bytes, have: {len(data)} bytes: [{repr(data)}]")
        in_crc32, = struct.unpack_from('<I', data, crc_start)

        my_crc32 = zlib.crc32(block_data)
        if my_crc32 != in_crc32:
            raise ValueError(f"CRC32 error block #{block_num}/{block_len}bytes need: {in_crc:08x} have: {my_crc32}")

        if block_num >= len(this.blocks):
            raise ValueError(f"Unexpected block #{block_num} out of {len(this.blocks)}")

        # good block
        if len(this.blocks[block_num]) > 0:
            print(f"Block #{block_num} already received, dupe ignored")
        
        this.blocks[block_num] = block_data
        print(f"Block #{block_num} happily received")

    # 0xAC 0xCE [buttmap]
    def status_packet_received(self, data):
        code = struct.unpack_from('>H', data, 0)
        if code != 0xACCE:
            raise ValueError(f"Unknown packet type received: {code:04h} bytes: [{repr(data)}]")
        status = data[2:]
        update = self.decode_status_map(status)
        
        missing = 0
        for n in range(len(update)):
            if update[n] != 0:
                self.send_map[n] = "#"
            else:
                missing += 1

        print(f"Transfer status: {missing}/{len(self.blocks)} unconfirmed\n{self.send_map}")

        if missing == 0:
            print(f"Sending {self.file_path} complete")
            state = STATE_FINISHED

        # good to go
        if state == STATE_INITIATE_SEND:
            state = STATE_SEND


    def tick(self):
        print(f"TICK: {self.session_id} S={self.state}")
        self.delay -= 1
        if self.state == STATE_INITIATE_SEND:
            if self.delay <= 0:
                # resend initial request until we receive ACCE or FECC
                self.delay = RESEND_INTERVAL_TICKS
                sendrq = self.make_send_request()
                print(f"sendrq: [{sendrq}]")
                self.iface.sendData(sendrq.encode("utf-8"), destinationId=self.destinationId, portNum=HELLO_APP, wantAck=False)
                #self.iface.sendData(sendrq.encode("utf-8"), destinationId=self.destinationId, portNum=HELLO_APP, wantAck=False)
            else:
                self.delay -= 1
        elif self.state == STATE_SEND:
            if self.delay <= 0:
                self.delay = BODY_INTERVAL_TICKS
                self.send_data_packet()
        elif self.state == STATE_RECEIVE:
            if self.delay <= 0:
                self.send_status_packet()
                self.delay = STATUS_INTERVAL_TICKS

    def send_status_packet(self):
        sid = [x for x in bytes.fromhex(self.session_id)]
        data = sid + [0xac, 0xce] + self.make_status_map()
        self.iface.sendData(bytes(data), destinationId=self.destinationId, portNum=DATA_APP, wantAck=False)
        print(f"send_status_packet: {[hex(b) for b in data]}")

    def matches(self, other):
        return False

class Scheduler:
    def __init__(self):
        self.sessions = {}

        self.iface = meshtastic.serial_interface.SerialInterface()
        info = self.iface.getMyNodeInfo()
        self.num = info["num"]
        self.id = info["user"]["id"]
        self.longName = info["user"]["longName"]
        self.shortName = info["user"]["shortName"]

        #self.my_node_num = self.iface.myInfo.myNodeNum
        print(f"Connected to: {self.shortName} {self.longName} {self.id}")
        pub.subscribe(self.on_receive, "meshtastic.receive")

    def on_receive(self, packet, interface):
        #print(repr(packet))
        portnum = packet.get("decoded", {}).get("portnum")
        payload = packet.get("decoded", {}).get("payload", b"")
        fromId = packet.get("fromId")
        toId = packet.get("toId")
        #print(f"packet.to={packet['to']} toId={toId}")
        if toId != self.id:
            return

        print(f"on_receive {fromId} portnum={portnum} payload={payload}")

        if portnum == 'TEXT_MESSAGE_APP' or portnum == HELLO_APP:
            text = packet["decoded"].get("text", "")
            print(f"TEXT_MESSAGE_APP text={text}")
            s = Session()
            if s.parse_initial_packet(text):
                # send request, remember session, initiate reception
                # TODO: find old session by file_path 
                if s.session_id in self.sessions:
                    old = self.sessions[s.session_id]
                    if old.matches(s):
                        print(f"Found old session {s.session_id} that matches {s.file_path}, ignoring new request")
                        pass # just continue the old session
                    else:
                        print(f"Old session {old.session_id} does not match new metadata for {s.file_path}, replacing old session")
                        old.abort()
                        self.sessions[s.session_id] = s
                        s.start_receive(interface, fromId)
                else:
                    print(f"New session {s.session_id} for file {s.file_path}")
                    self.sessions[s.session_id] = s
                    s.start_receive(interface, fromId)
        elif portnum == DATA_APP:
            sid = self.get_session_id(payload)
            print(f"DATA_APP sid={sid}")
            if sid in self.sessions:
                self.sessions[sid].packet_received(payload[4:])

    # returns 4-byte session id as 8-character string
    def get_session_id(self, bytes):
        sid = 0
        for b in bytes[:4]:
            sid = (sid << 8) | b
        return f"{sid:08x}"


    def send_file(self, path, recipient):
        s = Session(path)

        dstId, longId = get_destinationId(self.iface, recipient)
        if dstId == None:
            print(f"Could not find destinationId for [{recipient}]")
            return False

        print(f"Found {recipient}: {dstId} {longId}")

        s.start_send(self.iface, dstId, path)
        self.sessions[s.session_id] = s
        #def __init__(self, file_path = None, session_id = None, block_size = DEFAULT_BLOCK_SIZE):

    def sched(self):
        for s in self.sessions:
            session = self.sessions[s]
            session.tick()


def tets():
    s=Session()
    s.parse_initial_packet("KU! test.txt 1759273808 31474 64 1618607525 V001 b07c0356")
    s.start_receive(None, "!abcd")
    return s

if __name__ == '__main__':
    try:
        mode = sys.argv[1]
        if mode == "send":
            file, did = sys.argv[2:4]
    except Exception as e:
        print(f"Eggog: {e}")
        print("Usage: \n\tmeshku send filename.ext recipient-id\n\tmeshku receive")
        exit(1)

    scheduler = Scheduler()

    if mode == "send":
        scheduler.send_file(file, did)

    print("Created scheduler...")
    while True:
        scheduler.sched()
        time.sleep(TICK_TIME)








