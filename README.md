# mesh-ku
```
slowly send files over meshtastic


DATA_APP packets

Accept/status:
  session_id:u32 0xAC 0xCE [status map] (one status bit per packet)

General ack (e.g. sender acknowledges transfer completion)
  session_id:u32 0xBA 0xBE

Refusal:
  session_id:u32 0xFE 0xCC

Data:
  session_id:u32 0xDA 0xDA block-num:u16 byte-count:u8 [data] crc32:u32

Sender side
===========

initial-state

Creates session with session-id

expect-confirmation:

TEXT_MESSAGE_APP: `MK-SEND filename date size block-size crc32 version-string session-id`

sleep INIT-PACE, repeat

expect:
  <- see PRIVATE_APP 0xFECC = FECC OFF
  -> initial-state

  <- see PRIVATE_APP 0xACCE = ACCEPT
  -> main-state

block map:
  block[num] = send count

repeat until block map is empty
    pick a block from map
    -> PRIVATE_APP: session-id block-number byte-count [bytes] crc32
    sleep BLOCK-PACE

  expect:
    <- PRIVATE_APP: session-id 0xACCE [bitmap]  -- the bitmap is a big-endian byte array where each bit represents a block, 1 = received
    <- PRIVATE_APP: session-id 0xFECC -- cancel send


Receiver end
============

Expect:


  <- TEXT_MESSAGE_APP with destination = my node
      `MK-SEND filename date size block-size crc32 version-string session-id`

     if session exists, ignore

     create session session-id
       initialize bitmap

     session.receive()

  <- PRIVATE_APP: session_id .... -> sessions[session_id].handle_packet()

for session in sessions: session.loop()

session.loop():
  -> PRIVATE_APP: session-id 0xACCE [bitmap] -- update on all packets
  sleep BLOCK-PACE

session.handle_packet():
  if filemap[block_number] == 0:
    filemape[block_number] = payload
  
  


```


```mermaid
   stateDiagram-v2
        [*] --> StateA
        StateA --> StateB
        StateB --> StateC
        StateC --> [*]

        StateA : This is the first state
        StateB : This is the second state
        StateC : This is the final state
```        
