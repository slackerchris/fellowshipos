#!/usr/bin/env python3
"""Put a few real files in the harness's shares, so Files has something to list,
download and preview that a person can actually read. Pictures are written as
PNGs here rather than committed, so there is no binary in the repository."""
import os
import struct
import sys
import zlib


def png(width, height, pixel):
    rows = b"".join(b"\x00" + b"".join(pixel(x, y) for x in range(width)) for y in range(height))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def main(root):
    media = os.path.join(root, "media")
    photos = os.path.join(media, "photos")
    family = os.path.join(root, "family")
    archive = os.path.join(root, "archive")
    for folder in (media, photos, family, archive):
        os.makedirs(folder, exist_ok=True)
    with open(os.path.join(media, "notes.txt"), "w") as file:
        file.write("Things for the weekend\n\n"
                   "- ring the boat yard\n"
                   "- two bags of compost\n"
                   "- book the ferry before Friday\n")
    with open(os.path.join(photos, "sunrise.txt"), "w") as file:
        file.write("Taken from the top field, a little after five.\n")
    with open(os.path.join(archive, "old-notes.txt"), "w") as file:
        file.write("Kept for the record.\n")
    with open(os.path.join(family, "private.txt"), "w") as file:
        file.write("Only the people this share is for can read this.\n")
    with open(os.path.join(media, "holiday.png"), "wb") as file:
        file.write(png(320, 200, lambda x, y: bytes(
            (40 + x * 60 // 320, 90 + y * 90 // 200, 120 + (x + y) * 60 // 520))))
    with open(os.path.join(photos, "sunrise.png"), "wb") as file:
        file.write(png(320, 200, lambda x, y: bytes(
            (200 - y * 120 // 200, 120 + y * 80 // 200, 90 + x * 70 // 320))))


if __name__ == "__main__":
    main(sys.argv[1])
