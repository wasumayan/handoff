"""Shared logging setup, imported for its side effect: one stderr handler on the root logger."""
import logging
import sys

_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
root = logging.getLogger()
root.addHandler(_handler)
root.setLevel(logging.INFO)
