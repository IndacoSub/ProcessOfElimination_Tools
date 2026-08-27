#!/usr/bin/env python3
"""
repacker_simple.py
Sostituisce le stringhe nel DAT usando gli offset del CSV.
Supporta stringhe più corte o più lunghe tramite padding o truncation.
NON modifica indice, NON aggiorna comandi 0x10, NON fa shift.
"""

import csv
from pathlib import Path
import argparse

def parse_int(v: str) -> int:
    v = v.strip()
    return int(v, 16) if v.lower().startswith("0x") else int(v)

def load_replacements(csv_path: Path):
    repl = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="|", quotechar='"', escapechar="\\")

        required = {"kind", "text_offset", "text_length", "text"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit("CSV missing required columns: " + ", ".join(sorted(missing)))

        for row in reader:
            kind = (row["kind"] or "").strip().lower()
            if kind not in ("dialogue", "label"):
                continue

            new_text = row["text"]
            if new_text is None:
                continue

            # Restore escapes
            new_text = (
                new_text.replace(r"\r", "\r")
                        .replace(r"\n", "\n")
                        .replace(r"\|", "|")
            )

            new_bytes = new_text.encode("utf-8") + b"\x00"
            text_offset = parse_int(row["text_offset"])
            old_length = parse_int(row["text_length"])

            repl.append((text_offset, old_length, new_bytes))

    return repl

def apply_simple_replacements(data: bytearray, replacements):
    for text_offset, old_length, new_bytes in replacements:
        end = text_offset + old_length
        if end > len(data):
            raise SystemExit(f"Offset {text_offset:X} fuori dal file.")

        # Se nuova stringa è più corta → padding con 00
        if len(new_bytes) < old_length:
            padded = new_bytes + b"\x00" * (old_length - len(new_bytes))
            data[text_offset:end] = padded

        # Se nuova stringa è più lunga → truncation
        elif len(new_bytes) > old_length:
            truncated = new_bytes[:old_length]
            data[text_offset:end] = truncated

        else:
            # stessa lunghezza
            data[text_offset:end] = new_bytes

def main():
    ap = argparse.ArgumentParser(description="Repacker semplice con padding/truncation.")
    ap.add_argument("dat_file", help="File DAT originale")
    ap.add_argument("csv_file", help="CSV pipe-separated con text_offset e text")
    ap.add_argument("-o", "--output", help="Output DAT (default: <stem>_simple.dat)")
    args = ap.parse_args()

    dat_path = Path(args.dat_file)
    csv_path = Path(args.csv_file)

    if not dat_path.is_file():
        raise SystemExit(f"DAT non trovato: {dat_path}")
    if not csv_path.is_file():
        raise SystemExit(f"CSV non trovato: {csv_path}")

    output = Path(args.output) if args.output else dat_path.with_name(dat_path.stem + "_simple.dat")

    data = bytearray(dat_path.read_bytes())
    replacements = load_replacements(csv_path)

    apply_simple_replacements(data, replacements)

    output.write_bytes(data)
    print(f"Creato: {output}")
    print(f"Rimpiazzate {len(replacements)} stringhe con padding/truncation.")

if __name__ == "__main__":
    main()
