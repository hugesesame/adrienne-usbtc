"""Dump the 128-byte register space to map_<label>.txt and print it as a grid.

    python mapscan.py nosignal
    python mapscan.py withltc
    diff map_nosignal.txt map_withltc.txt

Capture states that differ in exactly one variable, then diff them. That is how
the status bits at 0x0C, 0x19 and 0x1A were found, and the three map_*.txt files
in this repository are the captures those conclusions came from.

Be aware of what this method cannot see: a flag that is zero in every state you
capture leaves no trace in any diff. The drop-frame bit in 0x10 was missed for
exactly that reason -- it only appears when drop-frame material is fed in. Before
concluding a flag must live in some unexplored register, check whether a field
you already understand has spare bits.

Addresses 0x7D and above are written as "--"; a 4-byte read cannot start there.
"""

import sys

from usbtc_reader import ADDR_MAX, UsbTc

label = sys.argv[1] if len(sys.argv) > 1 else "scan"
path = f"map_{label}.txt"

with UsbTc() as tc:
    tc.start()
    mem = tc.dump()

cell = lambda addr: "--" if addr > ADDR_MAX else f"{mem[addr]:02X}"

with open(path, "w") as f:
    for addr in range(0x80):
        print(f"{addr:02X} {cell(addr)}", file=f)

print("    " + " ".join(f"{c:02X}" for c in range(16)))
for row in range(0, 0x80, 16):
    print(f"{row:02X}: " + " ".join(cell(row + c) for c in range(16)))
print(f"\nsaved to {path}")
