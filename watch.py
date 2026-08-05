import time
from usbtc_reader import UsbTc

tc = UsbTc()
tc.start()

prev = None
for i in range(400):
    v = []
    for a in (0x0C, 0x0D, 0x10, 0x19, 0x1A, 0x4C):
        v.append(tc.command(bytes([0x74, a]), timeout=300)[2])
    cur = tuple(v[0:2] + v[3:])
    if cur != prev:
        print("0C=%s 0D=%s  TC.f=%02X  19=%s 1A=%s  4C=%02X" % (
            format(v[0], "08b"), format(v[1], "08b"), v[2],
            format(v[3], "08b"), format(v[4], "08b"), v[5]))
        prev = cur
    time.sleep(0.05)
