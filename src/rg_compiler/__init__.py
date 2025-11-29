from loguru import logger

from rg_compiler.logging_config import configure_logging

configure_logging()


def main() -> None:
    logger.info("rg-compiler CLI entrypoint invoked")
