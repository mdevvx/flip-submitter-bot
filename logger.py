import logging
from logging.handlers import RotatingFileHandler
import os

LOG_DIR = os.path.join(os.path.dirname(__file__), "data", "rotating_logs")
os.makedirs(LOG_DIR, exist_ok=True)


def get_logger(name=__name__, level=None):
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)
    fmt = logging.Formatter("%(asctime)s — %(levelname)s — %(name)s — %(message)s")

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = RotatingFileHandler(
        os.path.join(LOG_DIR, "bot.log"), maxBytes=5 * 1024 * 1024, backupCount=5
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger
