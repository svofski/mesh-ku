import os
import zlib
from pathlib import Path
import time
import secrets
import hashlib

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

MAX_RESEND=333
MESHKU_VERSION_STR="MKU001"

def get_timestamp(path):
    mod_time_float = os.path.getmtime(path)
    mod_time_int = int(mod_time_float)
    return str(mod_time_int)

def chunk_bytearray(data, chunk_size):
    """Yields chunks of a bytearray of a specified size."""
    for i in range(0, len(data), chunk_size):
        yield data[i:i + chunk_size]

class Session:
    def __init__(self, session_id, file_path, block_size = 64):
        if session_id == None:
            session_id = create_unique_session_id()[-8::]

        self.session_id = session_id
        self.file_path = file_path
        self.file_timestamp = get_timestamp(file_path)
            
        self.block_size = block_size
        self.blocks = []
        self.ready_to_send = False
        self.receiving = False
        self.finished = False

    def prepare_to_send(self):
        content = None
        with open(self.file_path, "rb") as file:
            content = bytearray(file.read())
            self.file_size = len(content)
            self.crc32 = zlib.crc32(content)

        if content != None:
            self.blocks = list(chunk_bytearray(content, self.block_size))

        self.resend_map = [MAX_RESEND] * len(self.blocks)

        self.ready_to_send = True

    def initial_packet(self):
        if not self.ready_to_send:
            return None

        file_name = Path(self.file_path).name
        return f"MK-SEND {file_name} {self.file_timestamp} {self.file_size} {self.block_size} {self.crc32} {MESHKU_VERSION_STR} {self.session_id}"

if __name__ == '__main__':
    s = Session(None, 'test.txt', 64)
    s.prepare_to_send()

    print(s.initial_packet())







