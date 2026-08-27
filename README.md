# ProcessOfElimination_Tools

Tools for research, extraction, and repacking of data files from the Nintendo Switch version of **Process of Elimination**.

> **Disclaimer:** This repository contains material created with the assistance of generative AI. Generative AI was used for reverse-engineering assistance, code development, analysis, and also for writing this README. The generated material has been reviewed and adapted as needed, but no guarantee is made regarding its completeness or correctness.

## Tools

### `dat_dialogue_extractor.py`

Extracts text entries from NIS `.dat` files into pipe-separated CSV files.

For `Talk.dat`, it extracts:

* `TALK_ID`
* record information
* command offsets
* text offsets
* text lengths
* dialogue text
* label/name text (`0x04`)
* dialogue text (`0x10`)

Example:

```text
python dat_dialogue_extractor.py "..\Data\Script\Talk.dat"
```

The generated CSV can be used as the basis for translation and further analysis.

### `talk_bytecode_analyzer.py`

Performs structural analysis of the `Talk.dat` bytecode.

It identifies recurring command patterns, collects opcode statistics, examines numeric parameters, and produces diagnostic reports to help reverse-engineer the format.

### `talk_repacker.py`

Reinserts translated dialogue from a pipe-separated CSV into a new `Talk.dat`.

The original file is not overwritten. When dialogue lengths change, the repacker rebuilds the payload and updates the corresponding index offsets.

Example:

```text
python talk_repacker.py "..\Data\Script\Talk.dat" ".\extract\Talk.csv"
```

## `Talk.dat` Format

`Talk.dat` uses a small indexed data format followed by a command-based bytecode stream.

The beginning of the file contains a 12-byte header:

```text
uint32  header_size
uint32  record_count
uint32  version / flag
```

For the examined `Talk.dat`, these values are:

```text
header_size  = 12
record_count = 2313
version      = 1
```

The header is followed by a table of `record_count` entries, each 12 bytes long:

```text
uint32  record_id
uint32  start
uint32  end
```

The offsets in this table are relative to the beginning of the data section. For the examined file, the data section starts at:

```text
0x6C78
```

Not every index entry is a `TALK_ID` record. Some entries contain a normal `TALK_ID_xxx` identifier, while others reference other kinds of event or command data.

### Text Commands

The text itself is stored inside the bytecode.

Two string-bearing commands have been identified:

```text
04 <uint16_le length> <UTF-8 text> 00
10 <uint16_le length> <UTF-8 text> 00
```

`0x10` is used for dialogue text. `0x04` is used for label/name-like text and is frequently associated with a following `0x10` dialogue command.

For example:

```text
04 04 00 E5 83 95 00
07 00 00 70 41
10 2A 00 49 27 6C 6C 20 73 74 61 79 ...
```

The `0x10` command therefore consists of:

```text
1 byte   opcode
2 bytes  UTF-8 field length, little-endian
N bytes  UTF-8 text including the terminating 00
```

The length is a byte count, not a character count.

### Other Commands

Several recurring numeric command patterns have also been identified during reverse engineering, including:

```text
06 + float32
07 + float32
08 + float32
09 + float32
```

and a recurring `0x11` pattern of the form:

```text
11 <byte> 5C F2 08 00
```

Their exact semantics are still under investigation and should not be considered fully documented.

## CSV Format

The dialogue extractor uses `|` as the CSV separator.

Relevant fields include:

```text
talk_id
kind
record_start
record_end
record_length
command_offset
text_offset
text_length
text_length_payload
text_index
text
```

`record_start` and `record_end` describe the indexed record.

`command_offset` points to the command opcode itself.

`text_offset` points to the first byte of the actual UTF-8 text.

`text_length` is the stored length of the text field, including the terminating null byte, while `text_length_payload` excludes that terminator.

For `Talk.dat`, `kind` currently distinguishes:

```text
label
dialogue
```

## Notes

These tools are part of an ongoing reverse-engineering effort. Some parts of the NIS data formats are understood, while other structures are still being investigated.

The bytecode documentation above describes structures that have been observed and reproduced from the examined files; opcode semantics that have not been confirmed are intentionally left unspecified.

## License

This repository is licensed under the **ISC License**.