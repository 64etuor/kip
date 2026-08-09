#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from kip.errors import ConfigurationError
from kip.settings import _environment_secret


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    arguments = parser.parse_args()
    try:
        value = _environment_secret(arguments.name)
    except ConfigurationError as error:
        print(f"secret loading failed: {error}", file=sys.stderr)
        return 1
    if not value:
        print(f"secret loading failed: {arguments.name} is not set", file=sys.stderr)
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
