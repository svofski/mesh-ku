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

import logging

from meshku import *

logger=None

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


def get_channel_stats(iface):
    node_info = iface.getMyNodeInfo()
    dm = node_info.get("deviceMetrics", {})
    ch_util = dm.get("channelUtilization")
    air_tx = dm.get("airUtilTx")
    return f"ChUtil: {ch_util:.2f}% AirUtilTx: {air_tx:.2f}%"
    
def send_immediate_refuse(iface, sid, destId, why=b""):
    sid = session_id_as_array(sid)
    data = sid + [0xfe, 0xcc];
    iface.sendData(bytes(data) + why, destinationId=destId, portNum=DATA_APP, wantAck=False)

def send_immediate_general_ack(iface, sid, destId):
    data = session_id_as_array(sid) + [0xba, 0xbe]
    iface.sendData(bytes(data), destinationId=destId, portNum=DATA_APP, wantAck=False)

def str_send_map(send_map, resend_map):
    # inf -> '#'
    pic = ".0123456789+"
    x = ['#' if math.isinf(x) else pic[min(c,len(pic)-1)] for x,c in zip(send_map, resend_map)]
    return ''.join(x)

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


def set_timestamp(path, timestamp_str):
    ts = int(timestamp_str)
    # set both atime and mtime to the same value
    os.utime(path, (ts, ts))

def get_timestamp(path):
    mod_time_float = os.path.getmtime(path)
    mod_time_int = int(mod_time_float)
    return str(mod_time_int)

def read_file(path):
    try:
        timestamp = get_timestamp(path)
        crc32 = 0
        content = None
        with open(path, "rb") as file:
            content = bytearray(file.read())
            crc32 = zlib.crc32(content)
        return content, timestamp, crc32
    except:
        return None, 0, 0

def chunk_bytearray(data, chunk_size):
    """Yields chunks of a bytearray of a specified size."""
    for i in range(0, len(data), chunk_size):
        yield data[i:i + chunk_size]

STATE_INITIAL = 0
STATE_INITIATE_SEND = 2
STATE_SEND = 3

STATE_RECEIVE = 10
STATE_RECEIVE_FINISHING = 11

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
        self.send_map = []      # smallest number gets sent, 
        self.resend_map = []
        self.retry_count = 0
        self.blocks_sent = 0
        self.blocks_received = 0
        self.blocks_rejected = 0
        self.start_time = 0

    def abort(self):
        if self.state == STATE_RECEIVE:
            self.state = STATE_ABORTED

    def prepare_to_send(self):
        if self.session_id == None:
            self.session_id = create_unique_session_id()[-8::]

        content, self.file_timestamp, self.crc32 = read_file(self.file_path)
        self.file_size = len(content)

        if content != None:
            self.blocks = list(chunk_bytearray(content, self.block_size))

        self.send_map = list(range(len(self.blocks)))
        self.resend_map = [0] * len(self.blocks)

        logger.debug(f"Prepared to send {self.file_path}: {len(self.blocks)} blocks, bs={self.block_size}")
        
    def make_send_request(self):
        file_name = Path(self.file_path).name
        return f"{HELLO_PREFIX} {file_name} {self.file_timestamp} {self.file_size} {self.block_size} {self.crc32} {MESHKU_VERSION_STR} {self.session_id}"

    # check if a local file already exists
    def same_file_exists(self):
        content, timestamp, crc32 = read_file(self.file_path)
        return timestamp == self.file_timestamp and crc32 == self.crc32 and len(content) == self.file_size

    def parse_initial_packet(self, text) -> bool:
        #print(f"parse_initial_packet: text=[{text}]")
        try:
            parts = text.split(" ")
            #print(f"parse_initial_packet: parts={repr(parts)}")

            if parts[0] != HELLO_PREFIX:
                return False

            self.file_path = parts[1]
            self.file_timestamp = parts[2]
            self.file_size = int(parts[3])
            self.block_size = int(parts[4])
            self.crc32 = int(parts[5])
            self.sender_version = parts[6]
            self.session_id = parts[7]

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
        nbits = (len(self.blocks) + 7) & ~7
        for n in range(nbits):
            yesno = int(len(self.blocks[n]) > 0) if n < len(self.blocks) else 1
            byte = (byte << 1) | yesno
            if (n + 1) % 8 == 0:
                filemap.append(byte)
                byte = 0
        return filemap

    # bytes from the status packet -> 1 number per block: 0 = missing, 1 = received
    def decode_status_map(self, bytes):
        filemap = []
        for n in range(len(bytes)):
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
        self.start_time = time.monotonic()
        logger.info(f"start_receive: from {senderId} expecting {len(self.blocks)} blocks")


    def start_send(self, iface, destinationId, path):
        self.iface = iface
        self.destinationId = destinationId
        self.state = STATE_INITIATE_SEND
        self.delay = 0
        self.prepare_to_send()

    # data is raw packet data with session_id stripped
    def packet_received(self, data):
        code, = struct.unpack_from('>H', data, 0)

        if code in [0xACCE, 0xFECC, 0xBABE]:
            self.status_packet_received(data)
        elif code == 0xDADA:
            self.data_packet_received(data)
        else:
            logger.debug(f"Unknown packet type: {code:04X}, IGNORE\n{dump(data)}") 

    # pick block with smallest value in send_map, excluding +inf blocks
    # replace picked index count with number of remaining blocks
    def pick_block_to_send(self, s):
        min_value = min(s)
        remaining = sum(1 for x in s if not math.isinf(x))
        if remaining == 0:
            return -1
        index = s.index(min_value)

        # decrement everything by 1
        for i in range(len(s)):
            s[i] = s[i] - 1
        
        s[index] = len(s) - 1  # make the picked block the last to retransmit
        return index

    # uint32_t session_id
    # uint16_t 0xDADA
    # uint16_t block-num (little-endian)
    # uint8_t byte-count
    # uint8_t array[byte-count]
    # uint32_t crc32
    def make_data_packet(self, block_num):
        header = session_id_as_array(self.session_id)
        header.append(0xda)
        header.append(0xda)
        header.append(block_num & 0xff);
        header.append((block_num >> 8) & 0xff);
        header.append(len(self.blocks[block_num]))
        crc32 = zlib.crc32(self.blocks[block_num])
        crc32_bytes = crc32.to_bytes(4, 'little')
        return bytes(header) + self.blocks[block_num] + crc32_bytes

    def send_data_packet(self):
        # pick a block to send
        pick = self.pick_block_to_send(self.send_map) 
        if pick == -1:
            self.state = STATE_FINISHED
            logger.warning(f"Sending {self.file_path} complete")
            return False
        self.resend_map[pick] += 1
        
        data = self.make_data_packet(pick)
        self.iface.sendData(data, destinationId=self.destinationId, portNum=DATA_APP, wantAck=False)
        self.blocks_sent += 1

        logger.debug(f"send_data_packet: sent block #{pick}")

    # uint16_t block-num
    # uint8_t byte-count
    # uint8_t array[byte-count]
    # uint32_t crc32
    def data_packet_received(self, _data):
        self.blocks_received += 1

        if self.state != STATE_RECEIVE:
            logger.debug(f"Unexpected data packet, REFUSE\n{dump(_data)}")
            send_immediate_refuse(self.iface, self.session_id, self.destinationId, b'Unexpected data packet')

        data = _data[2:] # strip 0xDADA from the start
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

        if block_num >= len(self.blocks):
            raise ValueError(f"Unexpected block #{block_num} out of {len(self.blocks)}")

        # good block
        if len(self.blocks[block_num]) > 0:
            logger.debug(f"Block #{block_num} is dupe, IGNORE")
            self.blocks_rejected += 1
            return
        
        self.blocks[block_num] = block_data
        logger.debug(f"Block #{block_num} happily received")
        self.delay = 0 # report status without waiting

        recv_map = "".join(['.' if len(b) == 0 else '#' for b in self.blocks])
        have = recv_map.count("#")
        logger.warning(f"Received {have}/{len(recv_map)} blocks: [{recv_map}] {get_channel_stats(self.iface)}");
        if recv_map.find('.') == -1:
            logger.warning(f"Received all blocks, finishing; {self.get_recv_stats()}")
            self.state = STATE_RECEIVE_FINISHING
            self.retry_count = 3
            self.save_received_file()
        

    # 0xAC 0xCE [buttmap]
    def status_packet_received(self, data):
        code, = struct.unpack_from('>H', data, 0)

        if self.state == STATE_RECEIVE_FINISHING:
            # am receiver, waiting for sender to acknowledge finished transfer
            if code == 0xBABE:
                self.state = STATE_FINISHED
                logger.warning(f"Sender acknowledged, finished session")
                return

        if not self.state in [STATE_INITIATE_SEND, STATE_SEND]:
            logger.debug(f"Unexpected status packet state={self.state}\n{dump(data)}")

        logger.debug(f"status_packet_received: code={code:04X}\n{dump(data)}")
        if code == 0xFECC:
            why = data[2:].decode("utf-8")
            logger.warning(f"Recipient refused: {why}, ABORT")
            self.state = STATE_ABORTED
            return

        if code != 0xACCE:
            logger.debug(f"Unknown packet type {code:04X}, IGNORE\n{dump(data)}")
        status = data[2:]
        update = self.decode_status_map(status)
        
        remain = 0
        for n in range(min(len(self.send_map), len(update))):
            if update[n] != 0:
                self.send_map[n] = math.inf # mark block as complete
            else:
                remain += 1
        sent = len(self.blocks) - remain

        logger.warning(f"Sent {sent}/{len(self.blocks)}: [{str_send_map(self.send_map, self.resend_map)}] {get_channel_stats(self.iface)}")

        if remain == 0:
            logger.warning(f"Sending {self.file_path} complete; {self.get_send_stats()}")
            send_immediate_general_ack(self.iface, self.session_id, self.destinationId)
            send_immediate_general_ack(self.iface, self.session_id, self.destinationId)
            send_immediate_general_ack(self.iface, self.session_id, self.destinationId)
            self.state = STATE_FINISHED

        # good to go
        if self.state == STATE_INITIATE_SEND:
            self.state = STATE_SEND


    def tick(self):
        #print(f"TICK: {self.session_id} S={self.state}")
        self.delay -= 1
        if self.state == STATE_INITIATE_SEND:
            if self.delay <= 0:
                # resend initial request until we receive ACCE or FECC
                self.delay = SEND_START_INTERVAL_TICKS
                sendrq = self.make_send_request()
                logger.debug(f"sendrq: [{sendrq}]")
                self.start_time = time.monotonic()
                self.iface.sendData(sendrq.encode("utf-8"), destinationId=self.destinationId, portNum=HELLO_APP, wantAck=False)
            else:
                self.delay -= 1
        elif self.state == STATE_SEND:
            if self.delay <= 0:
                self.delay = BODY_INTERVAL_TICKS + random.randint(-BODY_INTERVAL_SPREAD,BODY_INTERVAL_SPREAD)
                self.send_data_packet()
        elif self.state == STATE_RECEIVE:
            if self.delay <= 0:
                self.send_status_packet()
                self.delay = STATUS_INTERVAL_TICKS
        elif self.state == STATE_RECEIVE_FINISHING:
            if self.delay <= 0:
                self.send_status_packet()               # let sender know it's all good
                self.delay = STATUS_INTERVAL_TICKS
                self.retry_count -= 1
                if self.retry_count <= 0:
                    self.state = STATE_FINISHED

        return self.state != STATE_ABORTED and self.state != STATE_FINISHED

    def send_status_packet(self):
        sid = session_id_as_array(self.session_id)
        data = sid + [0xac, 0xce] + self.make_status_map()
        self.iface.sendData(bytes(data), destinationId=self.destinationId, portNum=DATA_APP, wantAck=False)
        logger.debug(f"send_status_packet:\n{dump(data)}")

    def save_received_file(self):
        contents = b''.join(self.blocks)
        my_crc32 = zlib.crc32(contents)
        if my_crc32 != self.crc32:
            raise ValueError(f"CRC32 error in {self.file_path} mine: {my_crc32:08x} theirs: {self.crc32}")

        fname = safe_filename(self.file_path)
        with open(fname, "wb") as f:
            f.write(contents)
        set_timestamp(self.file_path, self.file_timestamp)

    def get_send_stats(self):
        ratio = self.blocks_sent / len(self.blocks)
        elapsed = time.monotonic() - self.start_time
        cps = self.file_size / elapsed
        fmts = format_seconds(elapsed)
        text = f"blocks: {len(self.blocks)} sent: {self.blocks_sent} ratio: {ratio:.2f} elapsed: {fmts} cps: {cps:.1f}"
        return text

    def get_recv_stats(self):
        elapsed = time.monotonic() - self.start_time
        cps = self.file_size / elapsed
        fmts = format_seconds(elapsed)
        ratio = self.blocks_received / len(self.blocks)
        text = f"blocks: {len(self.blocks)} received: {self.blocks_received} ratio: {ratio:.2f} elapsed: {fmts} cps: {cps:.1f}"
        return text

class Scheduler:
    def __init__(self):
        self.sessions = {}
        self.refused_sessions = {}

        self.iface = meshtastic.serial_interface.SerialInterface()
        info = self.iface.getMyNodeInfo()
        self.num = info["num"]
        self.id = info["user"]["id"]
        self.longName = info["user"]["longName"]
        self.shortName = info["user"]["shortName"]

        #self.my_node_num = self.iface.myInfo.myNodeNum
        logger.warning(f"Connected to: {self.shortName} {self.longName} {self.id}")
        pub.subscribe(self.on_receive, "meshtastic.receive")

    def find_existing_session(self, newsession):
        for sid,s in self.sessions.items():
            if s.file_path == newsession.file_path and \
                s.file_size == newsession.file_size and \
                s.crc32 == newsession.crc32 and \
                s.block_size == newsession.block_size and \
                s.file_timestamp == newsession.file_timestamp:
                
                return sid, s

        return None, None


    def on_receive(self, packet, interface):
        #print(repr(packet))
        portnum = packet.get("decoded", {}).get("portnum")
        payload = packet.get("decoded", {}).get("payload", b"")
        fromId = packet.get("fromId")
        toId = packet.get("toId")
        #print(f"packet.to={packet['to']} toId={toId}")
        if toId != self.id:
            return

        #print(f"on_receive {fromId} portnum={portnum} payload={payload}")

        if portnum == 'TEXT_MESSAGE_APP' or portnum == HELLO_APP:
            text = packet["decoded"].get("text", "")
            logger.debug(f"TEXT_MESSAGE_APP text={text}")
            s = Session()
            if s.parse_initial_packet(text):
                # check if exactly the same file exists
                if s.same_file_exists():
                    send_immediate_refuse(self.iface, s.session_id, fromId, b'File already exists') 
                    self.refused_sessions[s.session_id] = s.session_id
                # send request, remember session, initiate reception
                old_sid, old_session = self.find_existing_session(s)
                if old_session == None:
                    logger.info(f"New session {s.session_id} for file {s.file_path}")
                    self.sessions[s.session_id] = s
                    s.start_receive(interface, fromId)
                else:
                    # discard the new session, update the old session with new id
                    if old_session.session_id != s.session_id:
                        old_session.session_id = s.session_id
                        self.sessions[s.session_id] = old_session
                        del self.sessions[old_sid]
                        self.refused_sessions[old_sid] = old_sid
                        logger.info(f"Found old session {old_sid}, updating to {s.session_id}")
        elif portnum == DATA_APP:
            sid = self.get_session_id(payload)
            code = 0
            try:
                code, = struct.unpack_from('>H', payload, 4)
            except:
                pass
            if sid in self.sessions:
                logger.debug(f"DATA_APP sid={sid} {code:04X} for existing session")
                self.sessions[sid].packet_received(payload[4:])
            else:
                if sid in self.refused_sessions:
                    logger.debug(f"DATA_APP sid={sid} is previously refused, IGNORE")
                else:
                    self.refused_sessions[sid] = sid
                    logger.debug(f"DATA_APP sid={sid} is unknown, REFUSE")
                    send_immediate_refuse(self.iface, sid, fromId, b'Unknown session')

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
            logger.warning(f"Could not find destinationId for [{recipient}]")
            return False

        logger.warning(f"Found {recipient}: {dstId} {longId}")

        s.start_send(self.iface, dstId, path)
        self.sessions[s.session_id] = s
        #def __init__(self, file_path = None, session_id = None, block_size = DEFAULT_BLOCK_SIZE):

    def sched(self):
        kills = []
        for s in self.sessions:
            session = self.sessions[s]
            if not session.tick():
                kills.append(session.session_id)

        for k in kills:
            logger.info(f"Cleaned up session {k}")
            del self.sessions[k]


def tets():
    s=Session()
    s.parse_initial_packet("KU! test.txt 1759394612 157 64 444085881 V001 c8e4a0f9")
    s.start_receive(None, "!abcd")
    return s

def tets2():
    s=Session()
    s.parse_initial_packet("KU! test.txt 1759394612 157 64 444085881 V001 c8e4a0f9")
    s.start_receive(None, "!abcd")
    s.blocks=[[1],[1],[],[],[1],[1],[1],[1],[],[1],[]]
    decoded = s.decode_status_map(s.make_status_map())
    print(f"status map decoded: {repr(decoded)}")
    return s

def tets3():
    s = Session('../test.txt')
    s.prepare_to_send()
    return s

def tets4():
    send = Session('../test.txt')
    send.prepare_to_send()
    req = send.make_send_request()

    recv = Session()
    recv.parse_initial_packet(req)
    recv.start_receive(None, "!abcd")

    packet = send.make_data_packet(2)
    recv.data_packet_received(packet[4:])

    return send, recv


if __name__ == '__main__':
    try:
        mode = sys.argv[1]
        if mode == "send":
            file, did = sys.argv[2:4]
            logger = setup_logger("send")
        else:
            logger = setup_logger("receive")
    except Exception as e:
        print("Usage: \n\tmeshku send filename.ext recipient-id\n\tmeshku receive")
        exit(1)

    scheduler = Scheduler()

    if mode == "send":
        scheduler.send_file(file, did)

    logger.debug("Created scheduler")
    while True:
        scheduler.sched()
        time.sleep(TICK_TIME)








