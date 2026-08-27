#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import struct
from dataclasses import dataclass
from pathlib import Path


def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


@dataclass
class Record:
    index: int
    record_id: int
    record_start: int
    record_end: int
    talk_id: str
    payload_start: int
    payload_end: int


def parse_talk_records(data: bytes):
    if len(data) < 12:
        raise ValueError("File too small.")

    header = u32(data, 0)
    count = u32(data, 4)
    if header != 12:
        raise ValueError(
            f"Unexpected Talk.dat header size: {header} (expected 12)."
        )

    data_base = 12 + count * 12
    if data_base > len(data):
        raise ValueError("Index table exceeds file size.")

    records = []
    for i in range(count):
        off = 12 + i * 12
        rid, rel_start, rel_end = struct.unpack_from("<III", data, off)

        a = data_base + rel_start
        b = data_base + rel_end

        if a > b or b > len(data):
            raise ValueError(f"Invalid record {i}: 0x{a:X}-0x{b:X}")

        raw = data[a:b].rstrip(b"\0")
        talk_id = raw.decode("utf-8", "replace")

        records.append(
            Record(
                index=i,
                record_id=rid,
                record_start=a,
                record_end=b,
                talk_id=talk_id,
                payload_start=b,
                payload_end=len(data),
            )
        )

    # Physical order is the order in which the associated bytecode appears.
    ordered = sorted(records, key=lambda r: r.record_start)

    for i, rec in enumerate(ordered):
        rec.payload_end = (
            ordered[i + 1].record_start
            if i + 1 < len(ordered)
            else len(data)
        )

    return data_base, records


def read_lp_text(data, pos, end, opcode):
    if pos + 3 > end or data[pos] != opcode:
        return None

    length = u16(data, pos + 1)
    text_start = pos + 3
    text_end = text_start + length

    if length < 1 or text_end > end:
        return None

    raw = data[text_start:text_end]

    if raw[-1:] != b"\0":
        return None

    try:
        text = raw[:-1].decode("utf-8")
    except UnicodeDecodeError:
        return None

    if not text:
        return None

    if not any(ch.isalpha() for ch in text):
        return None

    if not all(ch.isprintable() or ch in "\r\n\t" for ch in text):
        return None

    return length, text_start, text_end, text


def extract_talk(path):
    data = path.read_bytes()
    _, records = parse_talk_records(data)

    rows = []

    for rec in sorted(records, key=lambda r: r.record_start):
        pos = rec.payload_start
        text_index = 0

        while pos < rec.payload_end:
            for opcode, kind in ((0x04, "label"), (0x10, "dialogue")):
                hit = read_lp_text(data, pos, rec.payload_end, opcode)
                if hit:
                    length, text_start, text_end, text = hit

                    rows.append(
                        {
                            "file": path.name,
                            "record_index": rec.index,
                            "record_id": rec.record_id,
                            "talk_id": rec.talk_id,
                            "kind": kind,
                            "record_start": f"0x{rec.record_start:X}",
                            "record_end": f"0x{rec.record_end:X}",
                            "record_length": rec.record_end - rec.record_start,
                            "command_offset": f"0x{pos:X}",
                            "text_offset": f"0x{text_start:X}",
                            "text_length": length,
                            "text_length_payload": length - 1,
                            "text_index": text_index,
                            "text": text,
                        }
                    )

                    text_index += 1
                    pos = text_end
                    break
            else:
                pos += 1
                continue

            # for/else did not advance unless a command was found;
            # continue at the end of that command.
            continue

    return rows


def extract_null_strings(path, min_length=2):
    data = path.read_bytes()
    rows = []
    pos = 0
    index = 0

    while pos < len(data):
        end = data.find(b"\0", pos)
        if end < 0:
            break

        raw = data[pos:end]

        if len(raw) >= min_length:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = None

            if (
                text
                and any(ch.isalpha() for ch in text)
                and all(ch.isprintable() or ch in "\r\n\t" for ch in text)
            ):
                rows.append(
                    {
                        "file": path.name,
                        "record_index": -1,
                        "record_id": 0,
                        "talk_id": "",
                        "kind": "string",
                        "record_start": f"0x{pos:X}",
                        "record_end": f"0x{end + 1:X}",
                        "record_length": end + 1 - pos,
                        "command_offset": f"0x{pos:X}",
                        "text_offset": f"0x{pos:X}",
                        "text_length": len(raw) + 1,
                        "text_length_payload": len(raw),
                        "text_index": index,
                        "text": text,
                    }
                )
                index += 1

        pos = end + 1

    return rows


def write_pipe_csv(rows, output):
    fields = [
        "file",
        "record_index",
        "record_id",
        "talk_id",
        "kind",
        "record_start",
        "record_end",
        "record_length",
        "command_offset",
        "text_offset",
        "text_length",
        "text_length_payload",
        "text_index",
        "text",
    ]

    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            delimiter="|",
            quotechar='"',
            escapechar="\\",
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "text": row["text"]
                    .replace("|", r"\|")
                    .replace("\r", r"\r")
                    .replace("\n", r"\n"),
                }
            )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out-dir", default="extract")
    ap.add_argument("--mode", choices=("auto", "talk", "scan"), default="auto")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for filename in args.files:
        path = Path(filename)
        if not path.is_file():
            print(f"[ERROR] Not found: {path}")
            continue

        if args.mode == "talk":
            mode = "talk"
        elif args.mode == "scan":
            mode = "scan"
        else:
            mode = "talk" if "talk" in path.name.lower() else "scan"

        try:
            rows = extract_talk(path) if mode == "talk" else extract_null_strings(path)
            output = out_dir / f"{path.stem}.csv"
            write_pipe_csv(rows, output)
            print(f"{path.name}: {len(rows)} entries -> {output}")
        except Exception as exc:
            print(f"[ERROR] {path.name}: {exc}")


if __name__ == "__main__":
    main()
