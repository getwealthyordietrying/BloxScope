"""SYNTHETIC fixtures. These bytes are never real Roblox discoveries."""
import hashlib
import io
import json
from pathlib import Path
import struct
import zipfile

from bloxscope.common import ScopeError, now
from bloxscope.container import OBSERVED_PLAYER_PREFIX
from bloxscope.container_format import BINARY_RBX
from bloxscope.sources import CDN, SETTINGS


def pe_fixture():
    b = bytearray(0xc00)
    b[:2] = b"MZ"
    struct.pack_into("<I", b, 0x3c, 0x80)
    b[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", b, 0x84, 0x8664, 3)
    struct.pack_into("<H", b, 0x94, 0xf0)
    opt = 0x98
    struct.pack_into("<H", b, opt, 0x20b)
    struct.pack_into("<I", b, opt+16, 0x1000)
    struct.pack_into("<Q", b, opt+24, 0x140000000)
    struct.pack_into("<I", b, opt+108, 16)
    struct.pack_into("<II", b, opt+136, 0x3000, 12)
    for i, (name, rva, raw, length, flags) in enumerate(((b".text", 0x1000, 0x200, 0x200, 0x60000020), (b".rdata", 0x2000, 0x400, 0x600, 0x40000040), (b".pdata", 0x3000, 0xa00, 0x200, 0x40000040))):
        p = opt+0xf0+i*40
        struct.pack_into("<8sIIII", b, p, name, length, rva, length, raw)
        struct.pack_into("<I", b, p+36, flags)
    b[0x200:0x208] = b"\x48\x8d\x0d" + struct.pack("<i", 0x2000-0x1007) + b"\xc3"
    # Same valid opcode pattern outside the unwind ranges stays unverified.
    b[0x240:0x247] = b"\x48\x8d\x0d" + struct.pack("<i", 0x2000-0x1047)
    text = "SYNTHETIC Fixture Gift café 100%_[]".encode()
    b[0x400:0x400+len(text)] = text
    b[0x500:0x508] = struct.pack("<Q", 0x140002000)
    text16 = "café gift 漢字".encode("utf-16-le")
    b[0x600:0x600+len(text16)] = text16
    struct.pack_into("<III", b, 0xa00, 0x1000, 0x1008, 0x3100)
    b[0xb00] = 1
    return bytes(b)


def varint(n):
    out = bytearray()
    while n >= 128:
        out.append((n & 127) | 128)
        n >>= 7
    out.append(n)
    return bytes(out)


def table(strings):
    return b"\x09\x03" + varint(len(strings)) + b"".join(varint(len(s.encode()))+s.encode() for s in strings)


def chunk(tag, data):
    return struct.pack("<4sIII", tag, 0, len(data), 0)+data


def vector(items):
    return b"".join(struct.pack("<I", len(x))+x for x in items)


def named(class_id, name, kind, values):
    return struct.pack("<II", class_id, len(name))+name+bytes([kind])+values


def model_fixture(opaque=False):
    first = table(["SYNTHETIC FIXTURE", "café gift", "GiftFixture", "GiftFixture"])
    second = b"unknown" if opaque else OBSERVED_PLAYER_PREFIX + first
    return (BINARY_RBX+struct.pack("<HII", 0, 2, 3)+b"\0"*8
            + chunk(b"INST", named(7, b"ModuleScript", 0, struct.pack("<I", 2)+b"\0"*8))
            + chunk(b"PROP", named(7, b"Name", 1, vector([b"GiftFixture", b"GiftFixture"])))
            + chunk(b"PROP", named(7, b"Source", 29, vector([first, second])))
            + chunk(b"INST", named(8, b"Script", 0, struct.pack("<I", 1)+b"\0"*4))
            + chunk(b"PROP", named(8, b"Name", 1, vector([b"ReadableFixture"])))
            + chunk(b"PROP", named(8, b"Source", 1, vector(['-- SYNTHETIC FIXTURE\nlocal x = "café gift"\n'.encode()])))
            + chunk(b"END\0", b"</roblox>"))


def zip_fixture(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, value in files.items():
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, value)
    return out.getvalue()


class FakeHTTP:
    origin = "synthetic-test-fixture"

    def __init__(self, revision=1, fail=None):
        self.calls = []
        self.fail = fail
        self.bodies = {}
        for product, executable, archive in (("WindowsPlayer", "RobloxPlayerBeta.exe", "RobloxApp.zip"), ("WindowsStudio64", "RobloxStudioBeta.exe", "RobloxStudio.zip")):
            build = "version-"+f"{revision*2-(1 if product == 'WindowsPlayer' else 0):016x}"
            self.bodies[SETTINGS+product] = json.dumps({"clientVersionUpload": build, "version": "SYNTHETIC"}).encode()
            files = {archive: {executable: pe_fixture()},
                     "extracontent-models.zip": {"SyntheticFixture.rbxm": model_fixture()},
                     "extracontent-scripts.zip": {"Fixture.lua": ("-- SYNTHETIC FIXTURE\n"+"\n".join(f'Gift line {i} café 100%_[]' for i in range(35))+f"\nrevision {revision}\n").encode()}}
            records = ["v0"]
            for name, members in files.items():
                body = zip_fixture(members)
                self.bodies[f"{CDN}/{build}-{name}"] = body
                records.extend([name, hashlib.md5(body).hexdigest(), str(len(body)), str(sum(len(v) for v in members.values()))])
            self.bodies[f"{CDN}/{build}-rbxPkgManifest.txt"] = "\n".join(records).encode()

    def fetch(self, url, path, limit, progress=lambda _: None):
        self.calls.append(url)
        if self.fail and self.fail in url:
            raise ScopeError("SYNTHETIC download failure")
        body = self.bodies[url]
        if len(body) > limit:
            raise ScopeError("SYNTHETIC bound failure")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(body)
        return {"requested_url": url, "final_url": url, "observed_at": now(), "bytes": len(body), "fixture_data": True, "http_status": 200}
