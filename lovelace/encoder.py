"""Binary encode/decode for ZOIA .zoia patch files.

Adapted from zoia_lib (GPL-3.0, https://github.com/meanmedianmoge/zoia_lib).
The binary format is documented in zoia_lib/documentation/Binary Format.pdf.

Patch layout (32 KB file, zero-padded):
  [patch_size][patch_name][module_count][modules...][connection_count][connections...]
  [page_count][page_names...][starred_count][starred...][colors...]
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

from lovelace.modules import COLOR_ID, module_index

_INDEX = None

def _idx() -> dict:
    global _INDEX
    if _INDEX is None:
        _INDEX = module_index()
    return _INDEX


# ── Encoding helpers ──────────────────────────────────────────────────────────

def _enc_text(text: str, length: int) -> bytes:
    """Fixed-length ASCII text field, right-padded with nulls."""
    if text is None:
        text = ""
    text = text[:length]
    fmt = f"{len(text)}B{length - len(text)}x"
    return struct.pack(fmt, *text.encode("ascii", errors="replace"))


def _enc_value(value: int, length: int) -> bytes:
    """Little-endian unsigned integer, padded to `length` bytes."""
    if value == 0:
        value_bytes = 2
    else:
        value_bytes = int(math.ceil(math.log(value + 1, 2)) / 8)

    if value_bytes > 4:
        fmt, used = "<Q", 8
    elif value_bytes > 2:
        fmt, used = "<I", 4
    else:
        fmt, used = "<H", 2

    return struct.pack(fmt + f"{length - used}x", value)


def _enc_byte(byte: int, length: int) -> bytes:
    return struct.pack(f"B{length - 1}x", byte)


# ── Options bytes ─────────────────────────────────────────────────────────────

def _options_bytes(module: dict) -> list[int]:
    """Return up to 8 option byte values in definition order."""
    obs = module.get("options_binary", {})
    if isinstance(obs, list):
        return obs[:8]
    # dict keyed by stringified position index
    return [obs.get(str(i), 0) for i in range(8)]


# ── Encode ────────────────────────────────────────────────────────────────────

def encode(patch: dict, output_path: str | Path | None = None) -> bytes:
    """Encode a patch dict to a 32 KB .zoia binary.

    patch: dict with keys name, modules, connections, pages, pages_count,
           starred, colors, meta (n_modules, n_connections, n_starred, n_pages).
    """
    color_dict = {v: k for k, v in COLOR_ID.items()}  # id → name (unused here)
    name_to_id = COLOR_ID

    body = bytearray()

    # Patch name (16 bytes)
    body.extend(_enc_text(patch.get("name", "lovelace"), 16))

    # Module count
    modules = patch["modules"]
    body.extend(_enc_value(len(modules), 4))

    colors_arr = bytearray()

    for m in modules:
        mod_arr = bytearray()

        size = m["size"]
        mod_arr.extend(_enc_value(size, 4))
        mod_arr.extend(_enc_value(m["mod_idx"], 4))
        mod_arr.extend(_enc_value(m.get("version", 0), 4))   # unknown field
        mod_arr.extend(_enc_value(m["page"], 4))

        color = m.get("color", "Blue")
        color_id = m.get("header_color_id") or name_to_id.get(color, 1)
        mod_arr.extend(_enc_value(color_id, 4))

        pos = m["position"]
        first_pos = pos[0] if isinstance(pos, list) else pos
        mod_arr.extend(_enc_value(first_pos, 4))

        n_params = m.get("params", 0)
        mod_arr.extend(_enc_value(n_params, 4))
        mod_arr.extend(_enc_value(m.get("size_of_saveable_data", 0), 4))  # module version

        # Options: 8 bytes
        opt_bytes = _options_bytes(m)
        for ob in opt_bytes[:8]:
            mod_arr.extend(_enc_byte(ob, 1))
        if len(opt_bytes) < 8:
            mod_arr.extend(bytes(8 - len(opt_bytes)))

        # Parameters (raw, 4 bytes each)
        raw = m.get("parameters_raw", [])
        for v in raw[:n_params]:
            mod_arr.extend(_enc_value(int(v), 4))
        for _ in range(n_params - len(raw)):
            mod_arr.extend(_enc_value(0, 4))

        # Saved data
        saved = m.get("saved_data", [])
        if isinstance(saved, (bytes, bytearray)):
            saved_bytes = bytearray(saved)
        else:
            saved_bytes = bytearray(saved)

        # Compute expected saved data length from size
        expected_saved_words = size - 14 - n_params
        expected_saved_bytes = expected_saved_words * 4
        if len(saved_bytes) < expected_saved_bytes:
            saved_bytes.extend(b"\x00" * (expected_saved_bytes - len(saved_bytes)))
        elif len(saved_bytes) > expected_saved_bytes:
            saved_bytes = saved_bytes[:expected_saved_bytes]
        mod_arr.extend(saved_bytes)

        # Module name (16 bytes)
        mod_arr.extend(_enc_text(m.get("name", ""), 16))

        body.extend(mod_arr)

        # Color for this module (collected separately, appended after connections)
        colors_arr.extend(_enc_value(color_id, 4))

    # Connections
    connections = patch.get("connections", [])
    body.extend(_enc_value(len(connections), 4))

    # Colors go here (after connection count, before connection data per encoder order)
    # Actually per zoia_lib encoder: colors come AFTER all connections
    # Let's follow the exact zoia_lib encoder order:
    # connection_count + colors + connection_data is WRONG —
    # Looking at patch_encode.py more carefully:
    #   connections_array = encode_value(n_connections) + <colors loop interleaved?> + connections
    # Actually in patch_encode.py:
    #   for module, color_id in self._iter_colors(pch):
    #       colors_array.extend(...)  ← built during module loop
    #   connections_array.extend(connection_count_array)
    #   for connection in ...:
    #       connections_array.extend(...)
    # Then farray order is: name, module_count, modules, connections, pages, starred, colors
    # So colors come LAST.

    for conn in connections:
        if "strength_raw" in conn:
            src_m = conn.get("source_raw", 0)
            src_b = conn.get("source_block_raw", 0)
            dst_m = conn.get("dest_raw", 0)
            dst_b = conn.get("dest_block_raw", 0)
            strength = conn.get("strength_raw", 10000)
        else:
            src_m, src_b = map(int, conn["source"].split("."))
            dst_m, dst_b = map(int, conn["destination"].split("."))
            strength = int(round(conn.get("strength", 100) * 100))

        body.extend(_enc_value(src_m, 4))
        body.extend(_enc_value(src_b, 4))
        body.extend(_enc_value(dst_m, 4))
        body.extend(_enc_value(dst_b, 4))
        body.extend(_enc_value(strength, 4))

    # Pages
    pages = patch.get("pages", [])
    pages_count = patch.get("pages_count", len(pages))
    body.extend(_enc_value(pages_count, 4))
    for pg in pages[:pages_count]:
        body.extend(_enc_text(pg or "", 16))

    # Starred params
    starred = patch.get("starred", [])
    body.extend(_enc_value(len(starred), 4))
    for s in starred:
        body.extend(_enc_value(s["module"], 2))
        if s.get("midi_cc", "None") == "None":
            body.extend(_enc_value(s["block"], 2))
        else:
            cc = 128 * (s["midi_cc"] + 1) + s["block"]
            body.extend(_enc_value(cc, 2))

    # Colors (one per module)
    body.extend(colors_arr)

    # Patch size = total 4-byte words including the size word itself
    patch_size = len(body) // 4 + 1
    header = _enc_value(patch_size, 4)

    file_bytes = bytearray(header) + body

    # Pad to 32 KB (32768 bytes)
    if len(file_bytes) < 32768:
        file_bytes.extend(b"\x00" * (32768 - len(file_bytes)))
    else:
        file_bytes = file_bytes[:32768]

    if output_path is not None:
        Path(output_path).write_bytes(bytes(file_bytes))

    return bytes(file_bytes)


# ── Decode ────────────────────────────────────────────────────────────────────

def decode(data: bytes) -> dict:
    """Decode a .zoia binary into a patch dict (for inspect/round-trip)."""
    words = struct.unpack(f"<{len(data) // 4}I", data)

    patch_size = words[0]
    name_bytes = data[4:20]
    name = name_bytes.split(b"\x00")[0].decode("ascii", errors="replace")

    module_count = words[5]
    modules = []
    curr = 6

    idx = _idx()

    for i in range(module_count):
        size = words[curr]
        mod_idx = words[curr + 1]
        version = words[curr + 2]         # "unknown" field
        page = words[curr + 3]
        color_id = words[curr + 4]
        position = words[curr + 5]
        n_params = words[curr + 6]
        mod_version = words[curr + 7]     # "Module Version" per spec

        opts = list(data[(curr + 8) * 4 : (curr + 8) * 4 + 8])

        params_raw = [words[curr + 10 + p] for p in range(n_params)]

        saved_start = (curr + 10 + n_params) * 4
        name_start = (curr + size - 4) * 4
        saved = list(data[saved_start:name_start])

        mod_name_bytes = data[name_start : name_start + 16]
        mod_name = mod_name_bytes.split(b"\x00")[0].decode("ascii", errors="replace")

        color_names = {v: k for k, v in COLOR_ID.items()}
        color = color_names.get(color_id, "Blue")

        m_info = idx.get(str(mod_idx), {})

        modules.append({
            "number": i,
            "mod_idx": mod_idx,
            "type": m_info.get("name", f"unknown_{mod_idx}"),
            "category": m_info.get("category", ""),
            "cpu": m_info.get("cpu", 0),
            "name": mod_name,
            "size": size,
            "size_of_saveable_data": mod_version,
            "version": version,
            "page": page,
            "position": [position],
            "color": color,
            "header_color_id": color_id,
            "options_binary": {str(j): opts[j] for j in range(8)},
            "params": n_params,
            "parameters_raw": params_raw,
            "saved_data": saved,
            "blocks": {},
            "connections": [],
            "starred": [],
        })
        curr += size

    connection_count = words[curr]
    connections = []
    for j in range(connection_count):
        src_m = words[curr + 1]
        src_b = words[curr + 2]
        dst_m = words[curr + 3]
        dst_b = words[curr + 4]
        strength_raw = words[curr + 5]
        connections.append({
            "source": f"{src_m}.{src_b}",
            "destination": f"{dst_m}.{dst_b}",
            "strength": strength_raw // 100,
            "source_raw": src_m,
            "source_block_raw": src_b,
            "dest_raw": dst_m,
            "dest_block_raw": dst_b,
            "strength_raw": strength_raw,
        })
        curr += 5
    curr += 1

    pages_count = words[curr]
    pages = []
    for k in range(pages_count):
        pg_bytes = data[(curr + 1) * 4 : (curr + 1) * 4 + 16]
        pages.append(pg_bytes.split(b"\x00")[0].decode("ascii", errors="replace"))
        curr += 4
    curr += 1

    starred_count = words[curr]
    starred = []
    for _ in range(starred_count):
        raw32 = struct.unpack_from("<HH", data, (curr + 1) * 4)
        module_num = raw32[0]
        block_and_cc = raw32[1]
        midi_cc = (block_and_cc // 128) - 1 if block_and_cc >= 128 else "None"
        block = block_and_cc % 128 if block_and_cc >= 128 else block_and_cc
        starred.append({"module": module_num, "block": block, "midi_cc": midi_cc})
        curr += 1

    max_page = max((m["page"] for m in modules), default=0)
    n_pages = min(max_page + 1, 64)
    while len(pages) < n_pages:
        pages.append("")

    total_cpu = sum(m["cpu"] for m in modules)

    return {
        "name": name,
        "size": patch_size,
        "modules": modules,
        "connections": connections,
        "pages": pages,
        "pages_count": n_pages,
        "starred": starred,
        "colors": [],
        "meta": {
            "name": name,
            "cpu": round(total_cpu, 2),
            "n_modules": len(modules),
            "n_connections": len(connections),
            "n_pages": n_pages,
            "n_starred": len(starred),
        },
    }
