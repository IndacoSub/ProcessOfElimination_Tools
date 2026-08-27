#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path


def u16(data, o):
    return struct.unpack_from("<H", data, o)[0]


def u32(data, o):
    return struct.unpack_from("<I", data, o)[0]


def f32(data, o):
    return struct.unpack_from("<f", data, o)[0]


@dataclass
class Rec:
    index: int
    rid: int
    start: int
    end: int
    talk_id: str
    payload_end: int


def parse_records(data):
    count = u32(data, 4)
    base = 12 + count * 12
    recs = []
    for i in range(count):
        o = 12 + i * 12
        rid, s, e = struct.unpack_from("<III", data, o)
        a, b = base + s, base + e
        tid = data[a:b].rstrip(b"\0").decode("utf-8", "replace")
        recs.append((a, b, rid, i, tid))
    ordered = sorted(recs)
    out = []
    for j, (a, b, rid, i, tid) in enumerate(ordered):
        pend = ordered[j + 1][0] if j + 1 < len(ordered) else len(data)
        out.append(Rec(i, rid, a, b, tid, pend))
    return base, out


def lp(data, pos, end, opcode):
    if pos + 3 > end or data[pos] != opcode:
        return None
    n = u16(data, pos + 1)
    ts = pos + 3
    te = ts + n
    if n < 1 or te > end or data[te - 1] != 0:
        return None
    try:
        text = data[ts:te - 1].decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text or not any(ch.isalpha() for ch in text):
        return None
    if not all(ch.isprintable() or ch in "\r\n\t" for ch in text):
        return None
    return n, ts, te, text


def is_floatish(data, pos):
    if pos + 4 > len(data):
        return False
    x = f32(data, pos)
    if not math.isfinite(x):
        return False
    return abs(x) < 100000.0


def decode_command(data, pos, end):
    """
    Conservative recognition of the strongest signatures.

    Returns (kind, size, details) or None.
    """
    op = data[pos]

    # Confirmed strings.
    for opcode, kind in ((0x04, "TEXT_LABEL"), (0x10, "TEXT")):
        x = lp(data, pos, end, opcode)
        if x:
            n, ts, te, text = x
            return kind, te - pos, {
                "opcode": opcode,
                "length": n,
                "text_offset": ts,
                "text": text,
            }

    # Strong 07/08/09 + float32 forms.
    if op in (0x06, 0x07, 0x08, 0x09) and pos + 5 <= end and is_floatish(data, pos + 1):
        return f"OP_{op:02X}_F32", 5, {
            "opcode": op,
            "value": f32(data, pos + 1),
        }

    # 01 is seen in several fixed families. We do not assign a semantic name.
    # Common exact forms:
    #   01 64 00
    #   01 XX XX
    #   01 XX XX 06 <f32>
    if op == 0x01:
        if pos + 3 <= end:
            v16 = u16(data, pos + 1)
            if pos + 8 <= end and data[pos + 3] == 0x06 and is_floatish(data, pos + 4):
                return "OP_01_F16_F32TAG06", 8, {
                    "opcode": 1,
                    "value16": v16,
                    "tag": 6,
                    "value": f32(data, pos + 4),
                }
            return "OP_01_U16", 3, {
                "opcode": 1,
                "value16": v16,
            }

    # 00 01 xx 00 appears frequently as a control/state separator.
    if op == 0x00 and pos + 4 <= end and data[pos + 1] == 0x01 and data[pos + 3] == 0x00:
        return "OP_00_01_xx_00", 4, {
            "value8": data[pos + 2],
        }

    # 11 is not fully solved. The repeated
    #   11 <byte> 5C F2 08 00
    # is structurally distinct and worth reporting as a 6-byte instruction.
    if op == 0x11 and pos + 6 <= end:
        if data[pos + 2:pos + 6] == bytes.fromhex("5c f2 08 00"):
            return "OP_11_ID_5CF20800", 6, {
                "id8": data[pos + 1],
            }

    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("talk_dat")
    ap.add_argument("--out-dir", default="analysis")
    args = ap.parse_args()

    path = Path(args.talk_dat)
    data = path.read_bytes()
    base, records = parse_records(data)

    outdir = Path(args.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    command_counts = collections.Counter()
    command_sizes = collections.defaultdict(collections.Counter)
    command_examples = collections.defaultdict(list)
    transitions = collections.Counter()
    speaker_candidates = collections.Counter()
    text_counts_by_kind = collections.Counter()
    unknown_opcode_counts = collections.Counter()
    op11_ids = collections.Counter()
    numeric_values = collections.defaultdict(collections.Counter)

    # Per record disassembly summary.
    record_stats = []

    for rec in records:
        pos = rec.end
        stats = collections.Counter()
        prev_kind = None

        while pos < rec.payload_end:
            cmd = decode_command(data, pos, rec.payload_end)
            if cmd is None:
                unknown_opcode_counts[data[pos]] += 1
                pos += 1
                continue

            kind, size, details = cmd
            command_counts[kind] += 1
            command_sizes[kind][size] += 1
            stats[kind] += 1

            if len(command_examples[kind]) < 10:
                command_examples[kind].append(
                    {
                        "offset": hex(pos),
                        "record_index": rec.index,
                        "record_id": rec.rid,
                        "talk_id": rec.talk_id,
                        "bytes": data[pos:pos+size].hex(" "),
                        "details": details,
                    }
                )

            if prev_kind is not None:
                transitions[(prev_kind, kind)] += 1
            prev_kind = kind

            if kind == "TEXT_LABEL":
                text_counts_by_kind["label"] += 1
                speaker_candidates[details["text"]] += 1
            elif kind == "TEXT":
                text_counts_by_kind["dialogue"] += 1
            elif kind in ("OP_06_F32", "OP_07_F32", "OP_08_F32", "OP_09_F32"):
                numeric_values[kind][round(details["value"], 4)] += 1
            elif kind == "OP_11_ID_5CF20800":
                op11_ids[details["id8"]] += 1

            pos += size

        record_stats.append(
            {
                "record_index": rec.index,
                "record_id": rec.rid,
                "talk_id": rec.talk_id,
                "record_start": hex(rec.start),
                "record_end": hex(rec.end),
                "payload_end": hex(rec.payload_end),
                "stats": dict(stats),
            }
        )

    # Write concise text report.
    lines = []
    lines.append("NIS Talk.dat - structural bytecode analysis")
    lines.append("=" * 52)
    lines.append(f"File size: {len(data)} bytes (0x{len(data):X})")
    lines.append(f"Records: {len(records)}")
    lines.append(f"Data base: 0x{base:X}")
    lines.append("")
    lines.append("Recognized command signatures:")
    for k, c in command_counts.most_common():
        sizes = ", ".join(f"{s}B:{n}" for s, n in sorted(command_sizes[k].items()))
        lines.append(f"  {k:24s} {c:7d}  [{sizes}]")
    lines.append("")
    lines.append("Most common TEXT_LABEL values:")
    for t, c in speaker_candidates.most_common(50):
        lines.append(f"  {c:6d}  {t}")
    lines.append("")
    lines.append("Strong opcode transitions:")
    for (a, b), c in transitions.most_common(60):
        lines.append(f"  {c:6d}  {a} -> {b}")
    lines.append("")
    lines.append("OP_11 id byte values for pattern 11 xx 5C F2 08 00:")
    for x, c in op11_ids.most_common(80):
        lines.append(f"  {x:02X}: {c}")
    lines.append("")
    lines.append("Frequent numeric values:")
    for kind in sorted(numeric_values):
        lines.append(f"  {kind}")
        for x, c in numeric_values[kind].most_common(30):
            lines.append(f"      {x:g}: {c}")
    lines.append("")
    lines.append("Unknown first-byte counts (raw scan positions):")
    for x, c in unknown_opcode_counts.most_common(50):
        lines.append(f"  {x:02X}: {c}")

    (outdir / "talk_bytecode_phase4.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    obj = {
        "file": path.name,
        "size": len(data),
        "data_base": base,
        "records": len(records),
        "recognized_command_counts": dict(command_counts),
        "command_sizes": {
            k: dict(v) for k, v in command_sizes.items()
        },
        "command_examples": dict(command_examples),
        "transitions": [
            {"from": a, "to": b, "count": c}
            for (a, b), c in transitions.most_common()
        ],
        "label_candidates": dict(speaker_candidates),
        "opcode_11_id_values": dict(op11_ids),
        "numeric_values": {
            k: dict(v) for k, v in numeric_values.items()
        },
        "unknown_first_bytes": dict(unknown_opcode_counts),
    }

    (outdir / "talk_bytecode_phase4.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with (outdir / "talk_records_phase4.jsonl").open("w", encoding="utf-8") as f:
        for r in record_stats:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Human-readable samples: one representative command stream per selected
    # record, preserving exact offsets and raw bytes.
    sample_lines = []
    selected = 0
    for rec in records:
        # Prefer records with both label and dialogue.
        if selected >= 120:
            break

        pos = rec.end
        cmds = []
        has_label = has_text = False

        while pos < rec.payload_end and len(cmds) < 80:
            cmd = decode_command(data, pos, rec.payload_end)
            if cmd is None:
                pos += 1
                continue

            kind, size, details = cmd
            cmds.append((pos, size, kind, details))
            has_label |= kind == "TEXT_LABEL"
            has_text |= kind == "TEXT"
            pos += size

        if not (has_label and has_text):
            continue

        sample_lines.append(
            f"[record {rec.index}] ID={rec.rid} TALK_ID={rec.talk_id or '<none>'}"
        )
        for off, size, kind, details in cmds:
            raw = data[off:off + size].hex(" ")
            if "text" in details:
                suffix = f" {details['text']!r}"
            elif "value" in details:
                suffix = f" value={details['value']!r}"
            elif "id8" in details:
                suffix = f" id8=0x{details['id8']:02X}"
            else:
                suffix = ""
            sample_lines.append(f"  {off:08X}  {raw:<60} {kind}{suffix}")
        sample_lines.append("")
        selected += 1

    (outdir / "talk_disassembly_phase4.txt").write_text(
        "\n".join(sample_lines), encoding="utf-8"
    )

    print(f"Analyzed {len(records)} records.")
    print(f"Recognized command instances: {sum(command_counts.values())}")
    print(f"TEXT_LABEL: {command_counts['TEXT_LABEL']}")
    print(f"TEXT: {command_counts['TEXT']}")
    print(f"Report: {outdir}")


if __name__ == "__main__":
    main()
