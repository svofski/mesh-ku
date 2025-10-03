import os

DEFAULT_BLOCK_SIZE=128
MAX_RESEND_COUNT=333
MESHKU_VERSION_STR="V001"
HELLO_PREFIX="KU!"

# 0.25s schedulerticks
TICK_TIME=0.25

SEND_START_INTERVAL_TICKS=15//TICK_TIME
STATUS_INTERVAL_TICKS=10//TICK_TIME
BODY_INTERVAL_TICKS=4//TICK_TIME
# 1: 17.0cps ratio 4.5
# 2: 19.8cps ratio 2.38
# 3: 20.0cps ratio 1.62
# 4: 20.9cps ratio 1.25
# 5: 15.4cps ratio 1.38

# longer test (ADSKOK)
# 4: 19.3 ratio 1.59 10m24s sender 10m18s receiver
# 4 +/-3 randomizer: ratio: 1.33 elapsed: 9m 2s cps: 22.2
#   

# meshtastic port numbers 
TEXT_MESSAGE_APP = 1
PRIVATE_APP = 256 

# our port numbers
HELLO_APP=TEXT_MESSAGE_APP
DATA_APP=PRIVATE_APP+44


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
