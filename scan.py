import time
from usbtc_reader import UsbTc

tc = UsbTc()
tc.start()

print("scanning 0x74 0x00-0xFF ...\n")
hits = []
for sub in range(0x100):
    try:
        r = tc.command(bytes([0x74, sub]), timeout=300)
    except Exception as e:
        print(f"  {sub:02X} -- {e}")
        continue
    if not r or r[0] == 0xF0:
        continue
    hits.append((sub, r))
    print(f"  {sub:02X} -> {' '.join(f'{b:02X}' for b in r)}")
    time.sleep(0.01)

print(f"\n{len(hits)} responding subcommands")
