#!/usr/bin/env python3
"""
Repacker con progress bar per ogni step (usa tqdm).

Uso:
    python3 repacker.py Talk.dat Talk.csv -o Talk_repacked.dat

Opzioni:
    --include-labels    includi righe kind=label
    --only-changed      applica solo le righe che differiscono dal DAT
    --quiet             sopprimi output informativi (tqdm rimane visibile)
"""
from __future__ import annotations

import argparse
import csv
import shutil
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from tqdm import tqdm

@dataclass
class Replacement:
    text_offset: int
    old_length: int
    new_bytes: bytes
    command_offset: int
    record_index: int
    record_id: int
    talk_id: str
    text_index: int
    old_text: str
    new_text: str

    @property
    def text_end(self) -> int:
        return self.text_offset + self.old_length

    @property
    def delta(self) -> int:
        return len(self.new_bytes) - self.old_length

def parse_int(value: str) -> int:
    v = value.strip()
    return int(v, 16) if v.lower().startswith("0x") else int(v)

def parse_csv(csv_path: Path, include_labels: bool) -> List[Replacement]:
    replacements: List[Replacement] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="|", quotechar='"', escapechar="\\")
        required = {
            "kind", "record_index", "record_id", "talk_id",
            "command_offset", "text_offset", "text_length",
            "text_index", "text"
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit("CSV is missing required columns: " + ", ".join(sorted(missing)))

        for line_no, row in enumerate(reader, start=2):
            kind = (row.get("kind") or "").strip().lower()
            if kind != "dialogue" and not (include_labels and kind == "label"):
                continue

            new_text = row.get("text")
            if new_text is None:
                continue

            # Restore literal newline escapes and literal pipe
            new_text = new_text.replace(r"\r", "\r").replace(r"\n", "\n").replace(r"\|", "|")
            encoded = new_text.encode("utf-8") + b"\x00"

            if len(encoded) > 0xFFFF:
                raise SystemExit(f"{csv_path.name}:{line_no}: translated text is {len(encoded)} bytes; maximum is 65535.")

            old_length = parse_int(row["text_length"])
            text_offset = parse_int(row["text_offset"])
            command_offset = parse_int(row["command_offset"])

            replacements.append(
                Replacement(
                    text_offset=text_offset,
                    old_length=old_length,
                    new_bytes=encoded,
                    command_offset=command_offset,
                    record_index=int(row["record_index"]),
                    record_id=int(row["record_id"]),
                    talk_id=row["talk_id"],
                    text_index=int(row["text_index"]),
                    old_text="",
                    new_text=new_text,
                )
            )

    # Ensure unique mapping by text_offset
    by_offset = {}
    for r in replacements:
        if r.text_offset in by_offset:
            other = by_offset[r.text_offset]
            raise SystemExit(
                f"Duplicate text_offset in CSV: 0x{r.text_offset:X} (records {other.record_index} and {r.record_index})"
            )
        by_offset[r.text_offset] = r

    replacements.sort(key=lambda r: r.text_offset)
    return replacements

def parse_index(data: bytes):
    if len(data) < 12:
        raise SystemExit("DAT file is too small.")
    header_size, count, version = struct.unpack_from("<III", data, 0)
    if header_size != 12:
        raise SystemExit(f"Unexpected header size {header_size}; expected 12.")
    table_base = 12
    data_base = table_base + count * 12
    if data_base > len(data):
        raise SystemExit("Index table extends beyond DAT file.")
    records = []
    for i in range(count):
        o = table_base + i * 12
        record_id, rel_start, rel_end = struct.unpack_from("<III", data, o)
        records.append({
            "index": i,
            "record_id": record_id,
            "rel_start": rel_start,
            "rel_end": rel_end,
            "abs_start": data_base + rel_start,
            "abs_end": data_base + rel_end,
        })
    return header_size, count, version, data_base, records

def validate_replacements(data: bytes, data_base: int, records, replacements: List[Replacement]):
    by_index = {r["index"]: r for r in records}
    for r in replacements:
        if r.command_offset < 0 or r.text_offset < 0:
            raise SystemExit("Negative offset is invalid.")
        if r.text_offset + r.old_length > len(data):
            raise SystemExit(f"Text at 0x{r.text_offset:X} exceeds input file.")
        if r.command_offset + 3 > len(data):
            raise SystemExit(f"Command at 0x{r.command_offset:X} exceeds input file.")
        if data[r.command_offset] != 0x10:
            raise SystemExit(f"0x{r.command_offset:X}: expected opcode 0x10, found 0x{data[r.command_offset]:02X}.")
        stored_length = struct.unpack_from("<H", data, r.command_offset + 1)[0]
        if stored_length != r.old_length:
            raise SystemExit(f"0x{r.text_offset:X}: CSV text_length={r.old_length}, DAT stores {stored_length}.")
        if data[r.text_offset + r.old_length - 1 : r.text_offset + r.old_length] != b"\x00":
            raise SystemExit(f"0x{r.text_offset:X}: old field is not NUL-terminated.")
        rec = by_index.get(r.record_index)
        if rec is None:
            raise SystemExit(f"CSV record_index {r.record_index} does not exist.")
        if not (r.text_offset >= rec["abs_end"] and r.text_offset + r.old_length <= len(data)):
            raise SystemExit(f"Text offset 0x{r.text_offset:X} is not in the payload of record {r.record_index}.")
    for a, b in zip(replacements, replacements[1:]):
        if a.text_end > b.text_offset:
            raise SystemExit(
                "Overlapping replacements: "
                f"0x{a.text_offset:X}-0x{a.text_end:X} and 0x{b.text_offset:X}-0x{b.text_end:X}."
            )

def map_offset(old_offset: int, replacements: List[Replacement]) -> int:
    shift = 0
    for r in replacements:
        if old_offset >= r.text_end:
            shift += r.delta
        else:
            break
    return old_offset + shift

def copy_with_progress(src: Path, dst: Path, quiet: bool = False, chunk_size: int = 1024 * 1024):
    total = src.stat().st_size
    with src.open("rb") as fsrc, dst.open("wb") as fdst:
        with tqdm(total=total, unit="B", unit_scale=True, desc="Backup copy", disable=quiet) as pbar:
            while True:
                chunk = fsrc.read(chunk_size)
                if not chunk:
                    break
                fdst.write(chunk)
                pbar.update(len(chunk))

def write_bytes_with_progress(path: Path, data: bytes, quiet: bool = False, chunk_size: int = 4 * 1024 * 1024):
    total = len(data)
    with path.open("wb") as f:
        with tqdm(total=total, unit="B", unit_scale=True, desc="Writing output", disable=quiet) as pbar:
            mv = memoryview(data)
            offset = 0
            while offset < total:
                end = offset + chunk_size
                if end > total:
                    end = total
                f.write(mv[offset:end])
                pbar.update(end - offset)
                offset = end

def rebuild(data: bytes, replacements: List[Replacement], quiet: bool = False) -> bytes:
    # STEP 1 — Apply replacements (collect chunks)
    chunks: List[bytes] = []
    cursor = 0
    if replacements:
        with tqdm(total=len(replacements), desc="Applying replacements", disable=quiet) as pbar:
            for r in replacements:
                chunks.append(data[cursor:r.text_offset])
                chunks.append(r.new_bytes)
                cursor = r.text_end
                pbar.update(1)
    else:
        # no replacements: single chunk is the whole data
        chunks.append(data)

    # append tail
    if cursor < len(data):
        chunks.append(data[cursor:])

    # STEP 2 — Build payload (join chunks)
    # show a tiny progress bar to indicate the join step
    with tqdm(total=1, desc="Building payload", disable=quiet) as pbar:
        rebuilt = bytearray().join(chunks)
        pbar.update(1)

    # STEP 3 — Rewrite index table
    header_size, count, version, data_base, records = parse_index(data)
    if count > 0:
        with tqdm(total=count, desc="Updating index table", disable=quiet) as pbar:
            for rec in records:
                old_start = rec["abs_start"]
                old_end = rec["abs_end"]
                new_start = map_offset(old_start, replacements)
                new_end = map_offset(old_end, replacements)
                new_rel_start = new_start - data_base
                new_rel_end = new_end - data_base
                o = 12 + rec["index"] * 12
                struct.pack_into("<III", rebuilt, o, rec["record_id"], new_rel_start, new_rel_end)
                pbar.update(1)

    # STEP 4 — Update 0x10 commands
    if replacements:
        with tqdm(total=len(replacements), desc="Updating 0x10 commands", disable=quiet) as pbar:
            for r in replacements:
                new_command_offset = map_offset(r.command_offset, replacements)
                struct.pack_into("<H", rebuilt, new_command_offset + 1, len(r.new_bytes))
                pbar.update(1)

    return bytes(rebuilt)

def main():
    ap = argparse.ArgumentParser(description="Repack translated Talk.dat from pipe-separated CSV (with tqdm progress).")
    ap.add_argument("talk_dat", help="Original Talk.dat")
    ap.add_argument("csv_file", help="Translated pipe-separated CSV")
    ap.add_argument("-o", "--output", help="Output DAT path (default: <stem>_repacked.dat)")
    ap.add_argument("--include-labels", action="store_true", help="Also replace CSV rows with kind=label")
    ap.add_argument("--only-changed", action="store_true", help="Only replace rows where CSV text differs from DAT")
    ap.add_argument("--quiet", action="store_true", help="Suppress informational prints (progress bars still shown)")
    args = ap.parse_args()

    dat_path = Path(args.talk_dat)
    csv_path = Path(args.csv_file)

    if not dat_path.is_file():
        raise SystemExit(f"Input DAT not found: {dat_path}")
    if not csv_path.is_file():
        raise SystemExit(f"CSV not found: {csv_path}")

    output = Path(args.output) if args.output else dat_path.with_name(dat_path.stem + "_repacked" + dat_path.suffix)
    if output.resolve() == dat_path.resolve():
        raise SystemExit("Refusing to overwrite the original Talk.dat.")

    data = dat_path.read_bytes()
    header_size, count, version, data_base, records = parse_index(data)
    replacements = parse_csv(csv_path, args.include_labels)

    if args.only_changed:
        filtered: List[Replacement] = []
        for r in replacements:
            raw = data[r.text_offset:r.text_offset + r.old_length - 1]
            current = raw.decode("utf-8", errors="strict")
            if current != r.new_text:
                filtered.append(r)
        replacements = filtered

    validate_replacements(data, data_base, records, replacements)

    # Rebuild with progress
    rebuilt = rebuild(data, replacements, quiet=args.quiet)

    if len(rebuilt) < 12 + count * 12:
        raise SystemExit("Internal error: rebuilt file is too small.")

    # If output exists, back it up with progress
    if output.exists():
        backup = output.with_suffix(output.suffix + ".bak")
        if not args.quiet:
            print(f"Existing output found; creating backup: {backup}")
        try:
            copy_with_progress(output, backup, quiet=args.quiet)
        except Exception:
            # fallback to simple copy if something goes wrong
            shutil.copy2(output, backup)
            if not args.quiet:
                print("Backup copy fallback used (shutil.copy2).")

    # Write rebuilt file with progress
    write_bytes_with_progress(output, rebuilt, quiet=args.quiet)

    total_delta = len(rebuilt) - len(data)
    changed = sum(1 for r in replacements if len(r.new_bytes) != r.old_length)
    same_size = len(replacements) - changed

    if not args.quiet:
        print()
        print(f"Input : {dat_path}")
        print(f"CSV   : {csv_path}")
        print(f"Output: {output}")
        print(f"Records in index      : {count}")
        print(f"Dialogue fields changed: {len(replacements)}")
        print(f"  same byte length    : {same_size}")
        print(f"  resized             : {changed}")
        print(f"Original size         : {len(data)}")
        print(f"New size              : {len(rebuilt)}")
        print(f"Size delta            : {total_delta:+d}")

if __name__ == "__main__":
    main()
