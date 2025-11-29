from loguru import logger

from regelum.logging_config import configure_logging

configure_logging()


def main() -> None:
    logger.info("rg-compiler CLI entrypoint invoked")
