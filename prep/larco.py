"""LARCO on Zenodo: where the files are, and how to read single members of its zips.

The vibration archive is 13.5 GB, so nothing here downloads a whole zip. A zip
keeps its table of contents (the central directory) at the end: one ranged
request for the tail finds it, one more reads it, and each member is then
fetched with a single ranged request for its own bytes. Stdlib only.
"""
import http.client
import json
import os
import re
import struct
import time
import urllib.error
import urllib.request
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

RECORD = "https://zenodo.org/api/records/19666168"
FILES = {name: f"{RECORD}/files/{name}/content"
         for name in ("general.zip", "vibrations.zip", "LICENCE.txt")}

UNITS = ["becken_BWM5381IX", "becken-flt_BWM5381IX"]
LIBRARY_UNIT, DELIVERY_UNIT = UNITS

# wm_<unit>_<room>_<program>_<wash °C>_<load kg>.csv; some programs have no
# temperature (sterilization_0). The setting is everything after the room.
CYCLE = re.compile(r"^wm_(?P<unit>[^_]+_[^_]+)_(?P<room>cold|warm|hot)_(?P<setting>.+)\.csv$")


def _get(url, first=None, last=None, method="GET", attempts=8):
    """One request, retried on Zenodo's rate limit (429) and server errors."""
    headers = {} if first is None else {"Range": f"bytes={first}-{last}"}
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return r if method == "HEAD" else r.read()
        except urllib.error.HTTPError as err:
            if err.code != 429 and err.code < 500 or attempt == attempts - 1:
                raise
            wait = float(err.headers.get("Retry-After") or 0) or min(5 * 2 ** attempt, 120)
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError):
            if attempt == attempts - 1:
                raise
            wait = min(5 * 2 ** attempt, 120)
        time.sleep(wait)


def central_directory(url):
    """{member name: (compression method, compressed size, local header offset, size)}."""
    size = int(_get(url, method="HEAD").headers["Content-Length"])
    tail = _get(url, size - 65536, size - 1)
    i = tail.rfind(b"PK\x05\x06")
    cd_size, cd_offset = struct.unpack("<II", tail[i + 12:i + 20])
    if cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF:  # zip64 end record
        j = tail.rfind(b"PK\x06\x06")
        cd_size, cd_offset = struct.unpack("<QQ", tail[j + 40:j + 56])
    cd = _get(url, cd_offset, cd_offset + cd_size - 1)
    members, p = {}, 0
    while cd[p:p + 4] == b"PK\x01\x02":
        method = struct.unpack("<H", cd[p + 10:p + 12])[0]
        csize, usize = struct.unpack("<II", cd[p + 20:p + 28])
        name_len, extra_len, comment_len = struct.unpack("<HHH", cd[p + 28:p + 34])
        offset = struct.unpack("<I", cd[p + 42:p + 46])[0]
        name = cd[p + 46:p + 46 + name_len].decode()
        extra = cd[p + 46 + name_len:p + 46 + name_len + extra_len]
        k = 0
        while k + 4 <= len(extra):  # zip64 extra field: the 32-bit values that overflowed
            tag, n = struct.unpack("<HH", extra[k:k + 4])
            if tag == 1:
                q = 0
                if usize == 0xFFFFFFFF:
                    usize = struct.unpack("<Q", extra[k + 4 + q:k + 12 + q])[0]; q += 8
                if csize == 0xFFFFFFFF:
                    csize = struct.unpack("<Q", extra[k + 4 + q:k + 12 + q])[0]; q += 8
                if offset == 0xFFFFFFFF:
                    offset = struct.unpack("<Q", extra[k + 4 + q:k + 12 + q])[0]
            k += 4 + n
        members[name] = (method, csize, offset, usize)
        p += 46 + name_len + extra_len + comment_len
    return members


def fetch_member(url, entry):
    """The uncompressed bytes of one member, from its central-directory entry."""
    method, csize, offset, usize = entry
    header = _get(url, offset, offset + 29)
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    start = offset + 30 + name_len + extra_len
    raw = _get(url, start, start + csize - 1) if csize else b""
    data = zlib.decompress(raw, -15) if method == 8 else raw
    if len(data) != usize:
        raise IOError(f"size mismatch: got {len(data)} bytes, expected {usize}")
    return data


def listing(refresh=False):
    """The lab washing-machine cycles of both units, from the two archives' directories.

    Cached as data/listing.json, so the split can be made (and re-checked) from
    the listing alone, before any cycle is downloaded.
    """
    path = os.path.join(DATA, "listing.json")
    if os.path.exists(path) and not refresh:
        with open(path) as f:
            return json.load(f)
    general = central_directory(FILES["general.zip"])
    vibrations = central_directory(FILES["vibrations.zip"])
    by_base = {os.path.basename(n): (n, e) for n, e in vibrations.items() if n.endswith("_acc.parquet")}
    cycles = []
    for name, entry in sorted(general.items()):
        m = CYCLE.match(os.path.basename(name))
        if not (name.startswith("laboratory/washing_machine/") and m and m["unit"] in UNITS):
            continue
        vib = by_base.get(os.path.basename(name)[:-4] + "_acc.parquet")
        cycles.append({"file": os.path.basename(name), "unit": m["unit"], "room": m["room"],
                       "setting": m["setting"], "program": m["setting"].split("_")[0],
                       "labels": {"member": name, "entry": entry},
                       "vibration": {"member": vib[0], "entry": vib[1]} if vib else None})
    extras = {k: {"member": k, "entry": general[k]} for k in ("aggregated_data.csv", "metadata.xlsx")}
    out = {"record": RECORD, "cycles": cycles, "extras": extras}
    os.makedirs(DATA, exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    return out
