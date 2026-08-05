import sys, time
from usbtc_reader import UsbTc

tc = UsbTc()
tc.start()

mem = {}
for a in range(0x80):
    try:
        r = tc.command(bytes([0x74, a]), timeout=300)
    except Exception:
        continue
    if not r or r[0] in (0xF0, 0xF5):
        continue
    mem[a] = r[2]
    time.sleep(0.005)

label = sys.argv[1] if len(sys.argv) > 1 else "scan"
with open("map_" + label + ".txt", "w") as f:
    for a in range(0x80):
        v = mem.get(a)
        cell = "--" if v is None else "%02X" % v
        print("%02X %s" % (a, cell), file=f)

print("    ", end="")
for c in range(16):
    print("%02X " % c, end="")
print()
for row in range(8):
    print("%02X: " % (row*16), end="")
    for c in range(16):
        v = mem.get(row*16+c)
        print(("--" if v is None else "%02X" % v) + " ", end="")
    print()
print()
print("saved to map_" + label + ".txt")
