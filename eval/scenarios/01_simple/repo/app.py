import argparse
import logging
import sys

log = logging.getLogger("app")


def greet(name: str) -> str:
    return f"hello, {name}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--verbose", action="store_true", help="enable debug logging")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    log.info(greet(args.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
