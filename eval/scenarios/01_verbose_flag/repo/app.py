import argparse
import logging
import sys

import logsetup  # noqa: F401  (configures the root logger on import)

log = logging.getLogger("app")


def greet(name: str) -> str:
    return f"hello, {name}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--verbose", action="store_true", help="enable debug logging")
    args = ap.parse_args(argv)
    log.info(greet(args.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
