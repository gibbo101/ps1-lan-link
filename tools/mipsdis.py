import sys
from capstone import *
data=open("SLUS_00665.exe","rb").read()
TADDR=0x80010000; HDR=0x800
md=Cs(CS_ARCH_MIPS, CS_MODE_MIPS32+CS_MODE_LITTLE_ENDIAN)
def dis(addr,n,label):
    off=addr-TADDR+HDR
    print(f"\n===== {label}  @ {addr:08x} =====")
    for i in md.disasm(data[off:off+n*4], addr):
        print(f"  {i.address:08x}  {i.mnemonic:<8} {i.op_str}")
dis(int(sys.argv[1],16), int(sys.argv[2]), sys.argv[3])
