"""Read every address the read opcode accepts and print what answers.

This is the scan that cracked the protocol. The responses slide by one byte as
the address increments -- 74 00 gives CB AE 00 66, 74 01 gives AE 00 66 00 --
which is what showed that 0x74 takes an *address* rather than a subcommand
number. Everything else in this repository follows from that.

Addresses from 0x7D up answer F5: a 4-byte read starting there would overrun
the 128-byte space.
"""

import time

from usbtc_reader import ERR_MALFORMED, ERR_OUT_OF_RANGE, OP_READ, UsbTc

with UsbTc() as tc:
    tc.start()
    print(f"reading {OP_READ:02X} 00 .. {OP_READ:02X} FF\n")

    answered = []
    out_of_range = []

    for addr in range(0x100):
        try:
            r = tc.command(bytes([OP_READ, addr]), timeout=300)
        except Exception as exc:
            print(f"  {addr:02X}  -- {exc}")
            continue

        if not r or r[0] == ERR_MALFORMED:
            continue
        if r[0] == ERR_OUT_OF_RANGE:
            out_of_range.append(addr)
            continue

        answered.append(addr)
        print(f"  {addr:02X}  {r.hex(' ').upper()}")
        time.sleep(0.01)

    print(f"\n{len(answered)} addresses answered")
    if out_of_range:
        print(f"{len(out_of_range)} refused as out of range, "
              f"from 0x{out_of_range[0]:02X} up")
