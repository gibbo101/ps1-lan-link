import sys
BIN="~/Documents/development/ps1-lan-link/work/roms/retaliation-allies.bin"
SEC=2352; USER=2048
buf=bytearray(); found=False; out=bytearray()
with open(BIN,'rb') as f:
    idx=0
    while True:
        s=f.read(SEC)
        if len(s)<SEC: break
        # Mode2 Form1: 16-byte sync+header, 8-byte subheader, 2048 user data
        u=s[24:24+USER]
        if not found:
            if u[:8]==b'PS-X EXE':
                found=True
                out+=u
        else:
            out+=u
            if len(out) > 0x1a5800+0x800+SEC: break
        idx+=1
if not found:
    print("PS-X EXE not found"); sys.exit(1)
open("SLUS_00665.exe","wb").write(bytes(out))
import struct
pc0,gp,taddr,tsize=struct.unpack_from("<IIII",out,0x10)
print(f"PC0={pc0:08x} T_ADDR={taddr:08x} T_SIZE={tsize:08x} extracted={len(out)} bytes")
