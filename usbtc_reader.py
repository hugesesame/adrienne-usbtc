#!/usr/bin/env python3
"""
Adrienne Electronics USB-TC -- timecode reader for macOS and Linux.

The vendor ships Windows-only drivers and does not document the USB wire
protocol. This implementation is based on a protocol recovered by capturing
traffic from the vendor's Windows demo application and then mapping the
device's register space exhaustively.

The device is not a command-oriented peripheral. It exposes a 128-byte
register space that the host reads and writes:

    OUT ep 0x01 : 74 <addr>          read  -- returns 4 bytes from addr
                  61 <addr> <val>    write -- stores val at addr
    IN  ep 0x81 : 10 bytes           [cmd, addr, m[a], m[a+1], m[a+2],
                                      m[a+3], 00, 00, 00, 00]

Bytes 6-9 of every response are padding and are always zero. Reads slide by
one byte as the address increments, which is what gives the register model
away: 74 00 -> CB AE 00 66, 74 01 -> AE 00 66 00, and so on.

Error responses
    F0 ...  malformed command (e.g. a 1-byte write to the OUT endpoint)
    F5 ...  address out of range -- 0x7D and above overrun the 128-byte space

Register map (as far as it is currently understood)

    0x00-0x01   USB vendor ID, little-endian
    0x02-0x03   USB product ID, little-endian
    0x06-0x07   firmware revision, ASCII (e.g. "B1")
    0x08-0x0B   capability / model information -- NOT DECODED
    0x0C        status; bit 4 set while LTC is being received
    0x0D        toggles on read; heartbeat rather than data
    0x0E        0x01 once a signal has been seen
    0x10        timecode frames   (packed BCD)
    0x11        timecode seconds  (packed BCD)
    0x12        timecode minutes  (packed BCD)
    0x13        timecode hours    (packed BCD)
    0x14-0x17   zero in all captures so far; likely user bits, UNVERIFIED
    0x19        status; bit 7 flags a new frame, bits 6 and 0 always set
    0x1A        7-bit free-running frame counter, wraps at 0x7F
    0x2C        control register; the vendor driver writes 0x02 here to start
    0x4C        timing/phase measurement; cycles, low practical value

Two behaviours will cost you time if you write your own client:

  * The device enumerates in alternate setting 0, which exposes no endpoints.
    Call set_interface_altsetting() or every read fails with
    "Invalid endpoint address 0x81".

  * The first transaction after enumeration returns an empty response. The
    vendor driver issues redundant identify calls for exactly this reason.

Note that with no LTC present the device does not report an error on the
timecode registers -- it keeps returning the last value it decoded. Use the
lock bit at 0x0C rather than watching for the value to stop changing.

See README.md for the full protocol documentation.

Requires: libusb 1.0, pyusb.
License: MIT
"""

import time

import usb.core
import usb.util

VID, PID = 0xAECB, 0x6600
ALT = 1  # alt 1 = 8ms interval (ample for 30fps); alt 2 = 1ms

EP_OUT, EP_IN, RESP_LEN = 0x01, 0x81, 10

OP_READ  = 0x74  # 't'
OP_WRITE = 0x61  # 'a'

ERR_MALFORMED    = 0xF0
ERR_OUT_OF_RANGE = 0xF5

ADDR_VID      = 0x00
ADDR_PID      = 0x02
ADDR_FIRMWARE = 0x06
ADDR_CAPS     = 0x08
ADDR_STATUS   = 0x0C
ADDR_TIMECODE = 0x10
ADDR_USERBITS = 0x14  # unverified
ADDR_FRAMECTR = 0x1A
ADDR_CONTROL  = 0x2C

STATUS_LOCKED = 0x10  # 0x0C bit 4 -- LTC currently being received
CONTROL_RUN   = 0x02  # value the vendor driver writes to 0x2C

ADDR_MAX = 0x7C  # highest address a 4-byte read can start from

POLL_INTERVAL = 0.02  # vendor driver polled at ~47ms; 20ms oversamples 30fps


class UsbTcError(Exception):
    """Raised when the device reports a protocol-level error."""


def bcd(b):
    """Unpack a packed-BCD byte. Returns None if either nibble is invalid."""
    hi, lo = b >> 4, b & 0x0F
    return None if hi > 9 or lo > 9 else hi * 10 + lo


class UsbTc:
    """Register-level client for the Adrienne USB-TC over libusb.

    Usable as a context manager, which is the recommended form -- the device
    must be released cleanly or subsequent runs may fail with an I/O error
    until it is physically unplugged.

        with UsbTc() as tc:
            tc.start()
            print(tc.read_timecode())
    """

    def __init__(self, alt=ALT):
        self.dev = None
        self._open(alt)

    # -- lifecycle -----------------------------------------------------

    def _open(self, alt, allow_reset=True):
        self.dev = usb.core.find(idVendor=VID, idProduct=PID)
        if self.dev is None:
            raise UsbTcError("USB-TC not found (VID 0xAECB / PID 0x6600)")

        try:
            try:
                self.dev.set_configuration()
            except usb.core.USBError:
                pass  # already configured by a previous session
            # alt 0 exposes no endpoints -- this call is mandatory
            self.dev.set_interface_altsetting(interface=0, alternate_setting=alt)
        except usb.core.USBError:
            # A previous process most likely died holding the interface.
            # Reset once and retry before giving up.
            if not allow_reset:
                raise
            try:
                self.dev.reset()
            except usb.core.USBError:
                pass
            usb.util.dispose_resources(self.dev)
            time.sleep(0.5)
            return self._open(alt, allow_reset=False)

        # The first transaction after enumeration returns an empty response.
        try:
            self.command(bytes([OP_READ, ADDR_VID]))
        except (usb.core.USBError, UsbTcError):
            pass

    def close(self):
        """Release the device. Always call this, or use a `with` block."""
        if self.dev is None:
            return
        try:
            usb.util.release_interface(self.dev, 0)
        except Exception:
            pass
        usb.util.dispose_resources(self.dev)
        self.dev = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- transport -----------------------------------------------------

    def command(self, payload, timeout=500):
        """Send a raw command and return the 10-byte response."""
        self.dev.write(EP_OUT, payload, timeout=timeout)
        return bytes(self.dev.read(EP_IN, RESP_LEN, timeout=timeout))

    def read(self, addr, count=1):
        """Read `count` bytes (max 4) starting at `addr`."""
        if not 0 <= addr <= ADDR_MAX:
            raise ValueError(
                f"address 0x{addr:02X} out of range (0x00-0x{ADDR_MAX:02X})")
        if not 1 <= count <= 4:
            raise ValueError("count must be 1-4")
        r = self.command(bytes([OP_READ, addr]))
        if not r:
            raise UsbTcError(f"empty response reading 0x{addr:02X}")
        if r[0] == ERR_MALFORMED:
            raise UsbTcError("device rejected the command as malformed")
        if r[0] == ERR_OUT_OF_RANGE:
            raise UsbTcError(f"address 0x{addr:02X} out of range")
        return r[2:2 + count]

    def write(self, addr, value):
        """Write a single byte to `addr`.

        Only 0x2C is known to be safe. Writing to undocumented registers may
        change device state in ways this driver does not understand.
        """
        return self.command(bytes([OP_WRITE, addr, value]))

    def dump(self):
        """Return the full 128-byte register space as a bytearray.

        Unreadable addresses (0x7D and above) are left as zero.
        """
        mem = bytearray(0x80)
        for a in range(ADDR_MAX + 1):
            try:
                mem[a] = self.read(a)[0]
            except (UsbTcError, usb.core.USBError):
                pass
        return mem

    # -- device information --------------------------------------------

    def identify(self):
        """Return (vid, pid) as reported by the device itself."""
        r = self.read(ADDR_VID, 4)
        return r[0] | (r[1] << 8), r[2] | (r[3] << 8)

    def firmware(self):
        """Return the firmware revision string, e.g. "B1"."""
        return bytes(self.read(ADDR_FIRMWARE, 2)).decode("ascii", "replace")

    def capabilities(self):
        """Return the 4 capability bytes. Meaning not yet decoded."""
        return bytes(self.read(ADDR_CAPS, 4))

    # -- reader --------------------------------------------------------

    def start(self):
        """Enable the reader. Must be called before polling for timecode."""
        return self.write(ADDR_CONTROL, CONTROL_RUN)

    def locked(self):
        """True while LTC is actively being received.

        This is a hardware flag, so it is faster and more reliable than
        watching for the timecode to stop advancing -- and it correctly
        distinguishes a paused source from an absent one.
        """
        return bool(self.read(ADDR_STATUS)[0] & STATUS_LOCKED)

    def frame_counter(self):
        """7-bit free-running counter, incremented per frame, wraps at 0x7F."""
        return self.read(ADDR_FRAMECTR)[0] & 0x7F

    def read_timecode(self):
        """Return (hh, mm, ss, ff), or None if the registers hold invalid BCD.

        The value returned is whatever the device last decoded; it does not
        go stale on its own. Check locked() to know whether it is live.
        """
        r = self.read(ADDR_TIMECODE, 4)
        ff, ss, mm, hh = bcd(r[0]), bcd(r[1]), bcd(r[2]), bcd(r[3])
        if None in (ff, ss, mm, hh):
            return None
        return hh, mm, ss, ff

    def read_userbits(self):
        """Return the 4 bytes at 0x14.

        These are zero in every capture taken so far. They are *believed* to
        be the user bits, but this has not been verified against a source that
        actually transmits them.
        """
        return bytes(self.read(ADDR_USERBITS, 4))


def main():
    with UsbTc() as tc:
        vid, pid = tc.identify()
        print(f"device    : {vid:#06x}:{pid:#06x}")
        print(f"firmware  : {tc.firmware()}")
        print(f"caps      : {tc.capabilities().hex(' ').upper()}")
        tc.start()
        print("reader started -- Ctrl+C to stop\n")

        try:
            while True:
                try:
                    got = tc.read_timecode()
                    live = tc.locked()
                except (usb.core.USBError, UsbTcError) as exc:
                    print(f"\n  error: {exc}")
                    time.sleep(0.1)
                    continue

                if got is None:
                    time.sleep(POLL_INTERVAL)
                    continue

                hh, mm, ss, ff = got
                flag = "            " if live else "  [NO SIGNAL]"
                print(f"\r{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}{flag}",
                      end="", flush=True)
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\nstopped")


if __name__ == "__main__":
    main()