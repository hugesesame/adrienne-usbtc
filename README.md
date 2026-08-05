# Adrienne USB-TC — Register Map & Cross-Platform Reader

Reverse-engineered USB protocol for the **Adrienne Electronics USB-TC** family of
SMPTE/EBU timecode readers, with a Python reader for macOS and Linux and a
browser-based reader that needs no installation at all.

The vendor ships Windows-only drivers and has never released a macOS or Linux
version. The USB protocol is undocumented. This repository documents it.

**Have one of these readers? [Open it in your browser.](https://hugesesame.github.io/adrienne-usbtc/)**
Nothing to install, no driver, any OS — Chrome or Edge required.

[![The browser reader displaying timecode 06:59:52:07 at 30.00 fps](docs/screenshot.png)](https://hugesesame.github.io/adrienne-usbtc/)

> 日本語版は [README.ja.md](README.ja.md) を参照してください。

---

## Why this exists

The USB-TC series (`USB-LTC/RDR`, `USB-21VL/RDR`, `USB-IRIG/RDR`) are compact,
well-built hardware timecode readers. Units manufactured in the mid-2000s are
still perfectly functional, but the only way to talk to them is a Windows `.sys`
driver and a closed SDK DLL.

There is no technical reason for that limitation. The device is a
vendor-specific USB peripheral with firmware resident on board — no host-side
firmware upload, no kernel driver required. Any platform with libusb can drive
it directly from user space.

This document is the missing piece: the protocol and the register map.

---

## Device identification

```
idVendor        0xAECB      ("AECB" — an Adrienne Electronics vanity ID)
idProduct       0x6600
bDeviceClass    0xFF        vendor-specific
bDeviceSubClass 0xFF
bDeviceProtocol 0xFF
bcdUSB          0x0110      USB 1.1
bMaxPacketSize0 8
bMaxPower       100 mA, bus powered
Speed           Full Speed (12 Mbps)
```

Because the device class is `0xFF` at the device level, **no kernel driver on
any platform will claim it**. On macOS and Linux the interface is left
unclaimed, which makes libusb access straightforward and also satisfies the
requirements for **WebUSB**.

### Tested hardware

Everything here has been verified against two units, which between them span
fifteen years, two models and two firmware revisions:

| Serial | Built | Product string | Inputs | Firmware | `0x08` |
|---|---|---|---|---|---|
| `U200406281110` | 2004-06-28 | AEC USB-TC Time Code **and L21 Data** Reader | LTC + VIDEO | `B1` | `0x54` |
| `U201911181047` | 2019-11-18 | AEC USB-TC Time Code Reader | LTC only | `C1` | `0x10` |

Both report the same `idVendor`/`idProduct` and speak the identical protocol.
**The product ID does not distinguish the models — `0x08` does.** Anything
identifying a variant has to come from the register space, not the USB
descriptors.

The serial number appears to encode the build date: `U` + `YYYYMMDD` + a
sequence number.

---

## Interface layout

A single interface with **three alternate settings**:

| alt | Endpoints | bInterval | `iInterface` string |
|-----|-----------|-----------|---------------------|
| 0 | none | — | `Default 0KB/s Interface` |
| 1 | IN `0x81` / OUT `0x01`, 16 bytes each | 8 | `Alternate 1KB/s Interface` |
| 2 | IN `0x81` / OUT `0x01`, 16 bytes each | 1 | `Alternate 10KB/s Interface` |

Both endpoints are **interrupt** transfers with `wMaxPacketSize = 16`.

### Pitfall 1 — the device starts with no endpoints

**The device enumerates in alt 0, which exposes no endpoints.**

Calling `set_configuration()` alone leaves you in alt 0. Any attempt to read
endpoint `0x81` then fails with `Invalid endpoint address 0x81` (or the
equivalent on your stack). You must explicitly select an alternate setting:

```python
dev.set_interface_altsetting(interface=0, alternate_setting=1)
```

Alt 1 (8 ms polling) is more than sufficient for 30 fps timecode. Alt 2 (1 ms)
is presumably intended for field-rate VITC or Line 21 caption data.

Note that the Windows driver performs this selection inside
`URB_FUNCTION_SELECT_CONFIGURATION`, so a `SET_INTERFACE` request does **not**
appear as a standalone control transfer in a USB capture. Do not conclude from
its absence that alt selection is unnecessary.

### Pitfall 2 — the first transaction returns nothing

**The first transaction after enumeration returns an empty response.** Issue a
throwaway read and discard the result before doing anything meaningful. The
vendor driver makes three redundant identify calls at startup for what appears
to be exactly this reason.

---

## Protocol

This is **not** a command-oriented device. It exposes a **128-byte register
space** that the host reads and writes.

```
OUT ep 0x01 : 74 <addr>          read  — returns 4 bytes starting at addr
              61 <addr> <val>    write — stores val at addr
IN  ep 0x81 : 10 bytes
```

`0x74` is ASCII `'t'`; `0x61` is ASCII `'a'`.

### Response format

```
74 10 14 42 59 06 00 00 00 00
└──┬──┘ └─────┬─────┘ └───┬───┘
  echo    4 bytes from    padding
          addr            (always zero)
```

| Byte | Contents |
|------|----------|
| 0 | opcode echo |
| 1 | address echo |
| 2 | `mem[addr]` |
| 3 | `mem[addr+1]` |
| 4 | `mem[addr+2]` |
| 5 | `mem[addr+3]` |
| 6–9 | **padding — always zero** |

The register model is unmistakable once you sweep the address space: responses
slide by one byte as the address increments.

```
74 00  ->  CB AE 00 66
74 01  ->     AE 00 66 00
74 02  ->        00 66 00 00
74 03  ->           66 00 00 42
```

### Error responses

| Response | Meaning |
|----------|---------|
| `F0 00 00 ...` | **Malformed command** — e.g. a 1-byte write to the OUT endpoint |
| `F5 00 00 ...` | **Address out of range** — `0x7D` and above overrun the 128-byte space |

If you are probing blind and every value returns `F0`, your command *length* is
wrong, not your opcode. This is worth knowing: a one-byte probe sweep produces
`F0` for all 256 values and tells you nothing.

---

## Register map

Verified against three captured states — no signal, LTC running, LTC stopped —
and cross-checked against a second unit of a different model.

| Address | Contents | Confidence |
|---------|----------|------------|
| `0x00–0x01` | **USB vendor ID**, little-endian (`CB AE` = 0xAECB) | confirmed |
| `0x02–0x03` | **USB product ID**, little-endian (`00 66` = 0x6600) | confirmed |
| `0x04–0x05` | zero — unused? | — |
| `0x06–0x07` | **Firmware revision**, ASCII (`"B1"` and `"C1"` seen) | confirmed |
| `0x08` | **Capability flags.** bit 4 = LTC. bits 6 and 2 = the video-derived features, on video-capable units only | partly decoded |
| `0x09–0x0B` | `00 80 04` on both units tested; constant, not capability bits | — |
| `0x0C` | **Status — bit 4 set while LTC is being received** | high |
| `0x0D` | Toggles on read; heartbeat rather than data | medium |
| `0x0E` | `0x01` once a signal has been seen | medium |
| `0x0F` | Constant `0x74` | — |
| `0x10` | **Timecode frames**; BCD in bits 0–5, bit 6 **drop frame**, bit 7 colour frame | confirmed |
| `0x11` | **Timecode seconds**; BCD in bits 0–6, bit 7 polarity correction | confirmed |
| `0x12` | **Timecode minutes**; BCD in bits 0–6, bit 7 binary group flag | confirmed |
| `0x13` | **Timecode hours**; BCD in bits 0–5, bits 6–7 binary group flags | confirmed |
| `0x14–0x17` | Zero in every capture; **likely user bits** | unverified |
| `0x19` | Status — bit 7 flags a newly arrived frame; bits 6 and 0 always set | medium |
| `0x1A` | **7-bit free-running frame counter**, wraps at `0x7F` | high |
| `0x2C` | **Control register** — write `0x02` to enable the reader | confirmed |
| `0x4C` | Timing/phase measurement; cycles through a repeating set | low |
| others | zero | — |

### Observed behaviour by state

```
              0x0C        0x0D       0x19          0x1A       0x4C
LTC running   00010010    toggles    11000001 /    counting   cycling
                                     01000001
LTC stopped   00000010    toggles    01000001      frozen     0x08
No signal     00000010    toggles    00000000      0x00       0x28
```

The `0x1A` counter advances by 4–5 per polling pass. At roughly 60 ms per pass
and 33 ms per frame at 30 fps, that is exactly right.

### Timecode is BCD

Values at `0x10–0x13` are **packed BCD**, not binary. Frame counts advance
`0x18 → 0x20`, never `0x19 → 0x1A`. Byte `0x10` takes exactly 30 distinct
values across a 30 fps capture.

### Capability flags at `0x08`

Comparing the two units in [Tested hardware](#tested-hardware) isolates this
byte. It is the only difference between them that means anything — `0x06` holds
the firmware letter and `0x0D` is a heartbeat that changes on every read.

```
LTC + VIDEO unit   0x54 = 0101 0100    bit 6, bit 4, bit 2
LTC-only unit      0x10 = 0001 0000           bit 4
```

- **bit 4 — LTC.** Both units set it and both read LTC.
- **bits 6 and 2 — the video-derived features**, i.e. VITC and Line 21. Only the
  unit with a VIDEO BNC sets them; the LTC-only unit has no video connector at
  all, so it cannot support either.

Which of the two is VITC and which is Line 21 cannot be settled with these
units, because the one that has them has both. A model carrying VITC without
Line 21 would separate them immediately — if you own one, a single
`python mapscan.py yourlabel` would close this out.

### Mask the flag bits before unpacking

`0x10–0x13` are the **raw SMPTE words**, not decoded BCD. Each byte carries
digits *and* flag bits, so the flags have to be masked off first.

| Byte | Mask | Upper bits |
|---|---|---|
| `0x10` frames | `& 0x3F` | bit 6 = **drop frame**, bit 7 = colour frame |
| `0x11` seconds | `& 0x7F` | bit 7 = polarity correction |
| `0x12` minutes | `& 0x7F` | bit 7 = binary group flag |
| `0x13` hours | `& 0x3F` | bits 6–7 = binary group flags |

Skip the mask and drop-frame material reads frame 29 as `0x40 | 0x29 = 0x69`,
which unpacks as "69".

These flags are **invisible in a non-drop capture** — every upper bit is zero,
so no amount of diffing the dumps in this repository would have revealed them.
They only appear once drop-frame material is fed in. If a field looks fully
decoded, check whether it has spare bits before concluding a flag lives
somewhere else.

The frames → seconds → minutes → hours ordering is **identical to the serial
message format Adrienne published for the AEC-BOX-1/2/10/20 standalone readers**
in the late 1990s. The vendor carried its data layout forward unchanged into the
USB generation. That published specification is what made this protocol
tractable; if you are reverse-engineering another device in this family, read it
first — it is still available at `adrielec.com/box20lit.htm`.

### No-signal behaviour

**When the LTC input drops, the timecode registers do not report an error. They
retain the last value the device decoded.**

In one capture, `02:59:14:20` was returned unchanged for 7.36 seconds across 157
polls while the LTC source was stopped, then resumed counting on restoration.

Do not detect this with a staleness timeout. **Read the lock bit at `0x0C`
instead** — it is a hardware flag, so it responds immediately and correctly
distinguishes a paused source from an absent one.

### Vendor driver initialisation sequence

```
(USB standard)  GET_DESCRIPTOR device
(USB standard)  GET_DESCRIPTOR configuration
(USB standard)  SET_CONFIGURATION 1
74 00           read 0x00   (identify)
74 08           read 0x08   (capabilities)
74 00           read 0x00   (repeat)
74 00           read 0x00   (repeat)
74 04           read 0x04   (firmware revision region)
74 08           read 0x08   (repeat)
61 2C 02        write 0x02 to 0x2C   -- enables the reader
74 10 ...       poll 0x10, approximately every 47 ms
```

A minimal client needs only the throwaway read, `61 2C 02`, and then polling.

---

## Read timecode in a browser

**→ [hugesesame.github.io/adrienne-usbtc](https://hugesesame.github.io/adrienne-usbtc/)**

A single self-contained HTML page that reads the device over WebUSB and shows
the timecode full screen. Nothing to install, no driver, no server — open the
page, click **Connect Device**, pick the reader.

- Real measured frame rate to two decimals, so 29.97 is distinguishable from
  30.00 rather than guessed from the frame numbering.
- Drop frame written the conventional way, `01:23:45;12`.
- Signal presence taken from the hardware lock bit rather than inferred from a
  timeout, so it reacts immediately.

Requires **Chrome or Edge**. Safari does not support WebUSB and Firefox has
declined to implement it, so neither will work and that is unlikely to change.

The page is [`docs/index.html`](docs/index.html), and
[`docs/diag.html`](docs/diag.html) is a diagnostic page for when the device will
not connect. Both are plain static files with no dependencies — download them
and open them from disk if you would rather not load a page over the network.
Chrome treats `file://` as a secure context, so WebUSB works there too.

What makes this possible is the same property that made the vendor's Windows
driver necessary in the first place: the device is vendor-specific class, so no
kernel driver claims it and the browser can reach it directly. On Windows the
same page needs no WinUSB or Zadig shim either.

### Writing your own WebUSB client

Three things will cost you an evening if you do not know them:

- `selectAlternateInterface(0, 1)` is required, for the reason in
  [pitfall 1](#pitfall-1--the-device-starts-with-no-endpoints).
- So is the warm-up transaction from
  [pitfall 2](#pitfall-2--the-first-transaction-returns-nothing). Without it the
  first register read comes back as an empty packet and the connection looks
  like it failed.
- **An empty device chooser is usually correct behaviour, not a bug.** Chrome
  remembers permission per origin, and stops offering a device the origin has
  already been granted. Call `navigator.usb.getDevices()` and reuse the granted
  handle; only fall back to `requestDevice()` when there is none. Note also that
  `file://` and `http://localhost:8000` are separate origins with separate
  grants.

---

## Usage

### Requirements

- libusb 1.0
- Python 3 with pyusb

```bash
# macOS
brew install libusb

# Debian / Ubuntu
sudo apt install libusb-1.0-0

python3 -m venv .venv && source .venv/bin/activate
pip install pyusb
```

### Run

```bash
python usbtc_reader.py
```

```
device    : 0xaecb:0x6600
firmware  : B1
caps      : 54 00 80 04
reader started -- Ctrl+C to stop

06:59:49:02
```

`[NO SIGNAL]` appears when the lock bit is clear.

### As a library

```python
from usbtc_reader import UsbTc

with UsbTc() as tc:
    tc.start()
    if tc.locked():
        t = tc.read_timecode()
        print(t)                    # 01:23:45;12 -- semicolon means drop frame
        print(t.hh, t.mm, t.ss, t.ff, t.drop_frame, t.color_frame)

    # raw register access
    print(tc.read(0x10, 4).hex(" "))
    print(tc.dump().hex(" "))       # full 128-byte space
```

**Use the context manager.** The device must be released cleanly; a process
that dies holding the interface can leave it unusable until it is physically
unplugged. `UsbTc` also attempts a `reset()` and one retry if it finds the
interface already claimed.

### Linux permissions

```
# /etc/udev/rules.d/99-usbtc.rules
SUBSYSTEM=="usb", ATTRS{idVendor}=="aecb", ATTRS{idProduct}=="6600", MODE="0666"
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No backend available` | `export DYLD_LIBRARY_PATH=/opt/homebrew/lib:$DYLD_LIBRARY_PATH` (macOS) |
| `Access denied` | Run with `sudo` using the venv interpreter by absolute path, or add the udev rule |
| `Invalid endpoint address 0x81` | You skipped `set_interface_altsetting` |
| `[Errno 5] Input/Output Error` | A previous process did not release the interface. `UsbTc` retries with a reset; if that fails, unplug and reconnect |
| Identify returns `0x0000:0x0000` | You skipped the throwaway first read |
| `system_profiler SPUSBDataType` prints nothing | Known quirk on Apple Silicon; use `ioreg -p IOUSB -l -w 0` |

---

## Status and known gaps

**Working:** device identification, firmware revision, LTC timecode,
drop-frame and colour-frame flags, lock detection, frame counter, arbitrary
register read/write.

**Not yet decoded:**

- **A frame-rate field**, if one exists at all. Nothing in the register space
  is known to report 30 / 25 / 24 fps directly. In practice this does not
  block anything: the rate can be measured, and measuring is the only way to
  separate 29.97 from 30.00 regardless, since both label frames 0–29. Fit a
  line through `Timecode.frame_number()` against wall-clock time over a rolling
  window — differencing just the endpoints leaves a plus or minus one frame
  quantisation error, which is far too coarse for a 0.1% difference. Only
  30 fps material has been tested against the device so far.
- **Transport status** — direction, play / fast-forward / slow / stopped.
- **User bits.** `0x14–0x17` is the obvious candidate but reads zero in every
  capture, because no source transmitting user bits has been tested.
- **Which of `0x08` bit 6 and bit 2 is VITC and which is Line 21.** Both are
  set on the video-capable unit and clear on the LTC-only one, so the two
  cannot be told apart without a model that has one feature and not the other.
- **VITC** and **Line 21 / closed caption** data. The video-capable unit above
  has the VIDEO BNC for both; what is missing is an NTSC source to feed it.
- **Writable registers other than `0x2C`.** Unexplored, and risky to probe.

### Analysis tools

Three small scripts sit alongside the reader. They are what the register map
was built with, and they are the fastest way to extend it.

| Script | What it does |
|---|---|
| `scan.py` | Reads every address `0x00`–`0xFF` and prints the replies. This is the scan that showed `0x74` takes an address, not a subcommand number. |
| `mapscan.py` | Dumps the whole 128-byte space to `map_<label>.txt` and prints it as a grid. |
| `watch.py` | Prints a line whenever the watched registers change. Edit `WATCH` to follow different addresses. |

```bash
python mapscan.py nosignal      # with nothing on the LTC input
python mapscan.py withltc       # with the source running
diff map_unit1_nosignal.txt map_unit1_withltc.txt
```

The three `map_*.txt` files in this repository are the captures the register map
was derived from, taken in exactly that way.

### Contributing

The register space is only 128 bytes, so mapping is tractable:

```python
from usbtc_reader import UsbTc

with UsbTc() as tc:
    tc.start()
    mem = tc.dump()
    print(mem.hex(" "))
```

The productive method is **differential**: capture a full dump in two states
that differ in exactly one variable, then diff them. Isolating no-signal /
running / stopped is what identified the lock bit and the frame counter.

Know what that method cannot see. A flag that reads zero in every state you
capture leaves no trace in any diff, which is how the drop-frame bit in `0x10`
went unnoticed until drop-frame material was finally fed in. Before concluding
a flag must live in some unexplored register, check whether a field you already
understand has spare bits.

Captures and decodes are very welcome, particularly from anyone who can supply
25 or 24 fps sources, user bits, VITC, or an `USB-IRIG/RDR`.

One capture in particular would settle an open question in a single command: a
`mapscan.py` dump from **any unit that reads VITC but not Line 21**. That
separates bits 6 and 2 of `0x08`, which the two units here cannot.

Be aware that write commands to undocumented registers may change device state
in ways this driver does not understand. Dump the full register space first so
you can tell what moved.

---

## Method

The protocol was recovered in two stages.

**Stage 1 — USB capture.** Traffic between the vendor's Windows demo
application and the device was captured with USBPcap and correlated against
known timecode values. This established the two-byte command format and the BCD
layout.

**Stage 2 — address sweep.** Sweeping the second byte across `0x00–0xFF`
revealed that responses slide by one byte per increment — the second byte is an
*address*, not a subcommand. What had looked like a "read timecode command" was
simply a read of address `0x10`. Differential dumps across signal states then
filled in the status bits.

Reproducing the capture:

1. Install Wireshark with the **USBPcap** component selected (it is off by
   default), then reboot — USBPcap is a kernel driver.
2. Run Wireshark as Administrator and select the `USBPcap` interface
   corresponding to the device's root hub.
3. **Start the LTC source first**, then start the capture, then launch the
   vendor demo application. The initialisation sequence is what matters.
4. Stop and restart the LTC mid-capture to record the no-signal behaviour.

Feed a known, round timecode value such as `01:00:00:00`. Locating a constant
byte matching the hour immediately anchors the whole layout.

Captures can be parsed directly in Python without Wireshark. The pcapng link
type is 249 (`LINKTYPE_USBPCAP`). In the USBPcap packet header, the first two
bytes are the header length, offset 21 is the endpoint, offset 22 the transfer
type, offsets 23–26 the data length, and bit 0 of offset 16 is the direction
(1 = from device).

---

## Roadmap

**Rust / C / Node bindings.** The protocol is small enough to reimplement in an
afternoon in any language with libusb bindings.

**VITC and Line 21.** The hardware is here; an NTSC source to feed it is not.

---

## Disclaimer

This project is not affiliated with, endorsed by, or supported by Adrienne
Electronics Corporation. The protocol description was derived from observation
of publicly available software for interoperability purposes.

Provided as-is, with no warranty. Probing undocumented registers carries some
risk to any USB device; proceed accordingly.

---

## License

MIT
