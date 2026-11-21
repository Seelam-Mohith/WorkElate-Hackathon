"""Keep application logging on stderr without logging request contents."""

import logging
import sys


def configure_logging(level: str) -> None:
    logger = logging.getLogger("workelate")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
