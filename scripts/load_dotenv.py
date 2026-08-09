#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DotenvError(ValueError):
    pass


def _parse_value(raw: str, line_number: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as error:
            raise DotenvError(f"invalid quoted value on line {line_number}") from error
        if not isinstance(parsed, str) or "\n" in parsed or "\r" in parsed:
            raise DotenvError(f"dotenv values must be single-line strings on line {line_number}")
        return parsed
    comment = value.find(" #")
    if comment >= 0:
        value = value[:comment].rstrip()
    if "\x00" in value:
        raise DotenvError(f"dotenv value contains NUL on line {line_number}")
    return value


def parse_dotenv(path: Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not KEY_PATTERN.fullmatch(key):
            raise DotenvError(f"invalid dotenv assignment on line {line_number}")
        if key in seen:
            raise DotenvError(f"duplicate dotenv key on line {line_number}: {key}")
        seen.add(key)
        records.append((key, _parse_value(raw_value, line_number)))
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    arguments = parser.parse_args()
    try:
        for key, value in parse_dotenv(arguments.path):
            print(f"{key}={value}")
    except (DotenvError, OSError, UnicodeError) as error:
        print(f"dotenv loading failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
