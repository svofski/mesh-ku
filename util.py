import os, sys
import time
import zlib
from pathlib import Path
import secrets
import hashlib
import socket

def get_destinationId(iface, recipient):
    known_nodes = iface.nodes
    if not recipient in known_nodes:
        if "!" + recipient in known_nodes:
            recipient = "!" + recipient
        else:
            for node_id, node_data in known_nodes.items():
                if node_id[-4::] == recipient:
                    recipient = node_id
                    break
                user_info = node_data.get('user', {})
                long_name = user_info.get('longName', '')
                short_name = user_info.get('shortName', '')
                if long_name == recipient or short_name == recipient:
                    recipient = node_id
                    break

    if recipient in known_nodes:
        node_data = known_nodes[recipient]
        user_info = node_data.get('user', {})
        long_name = user_info.get('longName', 'N/A')
        return recipient, long_name

    return None, None


def session_id_as_array(sid):
    return [x for x in bytes.fromhex(sid)]

def dump(data):
    text = ""
    nlines = len(data)//16
    if len(data) & 15 != 0:
        nlines += 1
    for line in range(nlines):
        line_data = data[line * 16: line * 16 + 16]
        hexual = f"{line*16:04x}: "
        hexual += "".join([f"{x:02x}{'-' if i == 7 else ' '}" for i, x in enumerate(line_data)])
        charals = "".join([chr(x) if x > ord(' ') and x < 128 else '.' for x in line_data])

        text += f"{hexual:56s}  {charals:16s}\n"
        
    return text

def safe_filename(fname):
    base, ext = os.path.splitext(fname)
    counter = 1
    newname = fname
    while os.path.exists(newname):
        newname = f"{base}_{counter}{ext}"
        counter += 1
    return newname

def format_seconds(seconds: int) -> str:
    seconds = int(seconds)
    h, m = divmod(seconds, 3600)
    m, s = divmod(m, 60)

    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)

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


def short_hostname() -> str:
    hostname = socket.gethostname()
    return hostname.split('.')[0]
