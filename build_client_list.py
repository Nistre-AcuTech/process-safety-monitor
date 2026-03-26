#!/usr/bin/env python3
"""Scan Egnyte project folders to build a client name list for news matching.

Usage: python build_client_list.py [path_to_projects_folder]
Default path: Y:/Shared/Projects
"""

import json
import os
import sys

SKIP_PREFIXES = {"1 -", "New shortcut", "ZZ -", "ZZZ", "Z-PHAST", "BP - Shortcut"}
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "clients.json")


def scan_clients(base_path: str) -> list[str]:
    clients: set[str] = set()

    for entry in os.listdir(base_path):
        full = os.path.join(base_path, entry)
        if not os.path.isdir(full):
            continue

        # Process single-letter directories (A, B, C, ...)
        if len(entry) == 1 and entry.isalpha():
            for folder in os.listdir(full):
                folder_path = os.path.join(full, folder)
                if not os.path.isdir(folder_path):
                    continue
                name = folder.strip()
                if any(name.startswith(s) for s in SKIP_PREFIXES) or len(name) < 3:
                    continue
                clients.add(name)

    return sorted(clients, key=str.lower)


def main():
    base_path = sys.argv[1] if len(sys.argv) > 1 else "Y:/Shared/Projects"

    if not os.path.isdir(base_path):
        print(f"Error: {base_path} is not accessible")
        sys.exit(1)

    clients = scan_clients(base_path)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(clients, f, indent=2)

    print(f"Saved {len(clients)} client names to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
