#!/usr/bin/env python3
"""Check repository files for emoji glyphs (allow a small whitelist of neutral Unicode symbols).

Exits with code 0 if no disallowed emoji characters found. Exits with code 2 and prints
occurrences otherwise.
"""
import os
import re
import sys

# Emoji/unicode blocks commonly used for emoji presentation. Keep this focused
# to avoid matching box-drawing, CJK, or other non-emoji Unicode ranges used in
# README or third-party packages. Build the pattern from explicit ranges to
# keep readability and avoid accidental inclusion of unrelated glyph ranges.
emoji_ranges = [
    "\U0001F300-\U0001F5FF",  # Misc Symbols and Pictographs
    "\U0001F600-\U0001F64F",  # Emoticons
    "\U0001F680-\U0001F6FF",  # Transport and Map
    "\U0001F700-\U0001F77F",  # Alchemical Symbols
    "\U0001F780-\U0001F7FF",  # Geometric Shapes Extended
    "\U0001F900-\U0001F9FF",  # Supplemental Symbols and Pictographs
    "\U0001FA00-\U0001FA6F",  # Chess/etc and Symbols
    "\U0001FA70-\U0001FAFF",  # Symbols and Pictographs Extended-A
]

EMOJI_RE = re.compile("[" + "".join(emoji_ranges) + "]")

# Whitelist of neutral Unicode symbols allowed by project policy (examples).
ALLOWED = set([
    "●",
    "○",
    "*",
    "✔",
    "⚠",
    "!",
    "★",
    "✱",
])

INCLUDE_EXTS = {"py", "md", "sh", "txt", "yaml", "yml", "json", "ini", "cfg", "toml"}
SKIP_DIRS = {".git", "venv", "env", "node_modules", "__pycache__", "dist", "build", ".venv", ".tox"}


def is_text_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            f.read(1024)
        return True
    except Exception:
        return False


def scan_file(path):
    matches = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                for m in EMOJI_RE.finditer(line):
                    ch = m.group(0)
                    # If all chars in match are in the allowed set, skip
                    if all(c in ALLOWED for c in ch):
                        continue
                    col = m.start() + 1
                    matches.append((lineno, col, ch, line.rstrip('\n')))
    except Exception:
        # Ignore files we can't read
        return []
    return matches


def walk_and_scan(root):
    findings = {}
    for dirpath, dirnames, filenames in os.walk(root):
        # modify dirnames in place to skip unwanted dirs
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            ext = fname.rsplit('.', 1)[-1] if '.' in fname else ''
            if ext and ext not in INCLUDE_EXTS:
                continue
            path = os.path.join(dirpath, fname)
            if not is_text_file(path):
                continue
            m = scan_file(path)
            if m:
                findings[path] = m
    return findings


def main():
    root = os.getcwd()
    findings = walk_and_scan(root)
    if not findings:
        print("No disallowed emoji characters found.")
        return 0

    print("Found disallowed emoji characters:\n")
    for path, items in findings.items():
        for lineno, col, ch, line in items:
            print(f"{path}:{lineno}:{col}: '{ch}'  -> {line}")
    print("\nPlease replace emoji glyphs with allowed neutral Unicode symbols per repository policy.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
