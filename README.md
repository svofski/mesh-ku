mesh-ku
=======

Send files without haste using Meshtastic.

```
    __
 ",/__\_    ... 20 cps .  .     .
   `  `
```

mesh-ku works via Meshtastic Python API on Serial interface. In order to use it you need nodes connected to computers via serial, up to date Python with Meshtastic library installed. This is experimental software.

Basic usage
-----------

Wait for incoming files. Files will be saved in the current directory.

`python3 session.py receive`

Send a file. Destination can be a short or long name or full node id. Path to file will be stripped.

`python3 session.py send path/to/file.ext <destination>`



Packet types
------------

## TEXT_MESSAGE_APP packets

### INITIATE

  `KU! filename.ext unix-time file-size block-size file-crc32 version-string session-id`

The SENDER sends **INITIATE** packets until receiving **ACCEPT** or **REFUSE** from the RECIPIENT.

Upon receiving **ACCEPT** SENDER proceeds with sending file using block map from **ACCEPT** packets as feedback.

**REFUSE** -> SENDER stops

**REFUSE** can mean that the file already exists and size/CRC32 match.

## DATA_APP packets

DATA_APP packets are sent in PRIVATE_APP+48 (300) and are byte encoded.

### ACCEPT

  `session_id:u32 0xAC 0xCE [status map]` 

Status map contains one status bit per packet, starting with MSB, LSB padded as necessary to complete the byte. 1 in the map means packet was received and does not need a resend.

**ACCEPT** packets are general status updates and don't have to follow each packet reception. They could be sent once per 10 seconds or even a minute, for example.

### GENERAL ACK
  
  `session_id:u32 0xBA 0xBE`

Sender acknowledges transfer completion.

### REFUSE

  `session_id:u32 0xFE 0xCC b'byte-encoded reason'`

### DATA

  `session_id:u32 0xDA 0xDA block-num:u16 byte-count:u8 [data] crc32:u32`

Data from SENDER to RECIPIENT.

Example session
---------------

1. Sender creates a Session and sends **INITIAL** packet
2. Recipient responds with **ACCEPT**
3. Sender proceeds sending **DATA**
4. Recipient sends feedback using **ACCEPT**
5. Sender resends packets that were missing in **ACCEPT**
6. Recipient receives full file and saves it. Sends final **ACCEPT** with status map bits all set.
7. Sender understands full map as transfer completion and sends a **GENERAL ACK** mostly just to be polite.

