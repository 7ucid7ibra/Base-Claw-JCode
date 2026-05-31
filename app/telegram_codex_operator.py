from __future__ import annotations

import asyncio

from telegram_operator import *  # noqa: F401,F403
from telegram_operator import LOGGER, main


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        LOGGER.exception("BaseClaw Telegram operator crashed")
        raise
