"""Print a line whenever the watched registers change.

Run this while starting and stopping the LTC source. That is how the lock bit
at 0x0C bit 4 was identified: it is the only bit that tracks the presence of a
signal rather than the passage of frames.

Edit WATCH to follow different addresses. Anything listed in WATCH but not in
TRIGGER is shown for context without causing a line of its own -- 0x10 holds
the timecode frame number, so triggering on it would print on every sample and
drown out everything else.
"""

import time

from usbtc_reader import UsbTc

WATCH = (0x0C, 0x0D, 0x10, 0x19, 0x1A, 0x4C)
TRIGGER = (0x0C, 0x0D, 0x19, 0x1A, 0x4C)
SAMPLES = 400
INTERVAL = 0.05

with UsbTc() as tc:
    tc.start()
    print("  ".join(f"{addr:02X}".center(8) for addr in WATCH))

    previous = None
    for _ in range(SAMPLES):
        values = {addr: tc.read(addr)[0] for addr in WATCH}
        current = tuple(values[addr] for addr in TRIGGER)

        if current != previous:
            print("  ".join(f"{values[addr]:08b}" for addr in WATCH))
            previous = current

        time.sleep(INTERVAL)
