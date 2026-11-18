"""Translate service SIGTERM into orderly async cleanup and a successful stop."""

import asyncio
import signal


async def run_service(coroutine):
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(coroutine)
    requested = False
    installed = False

    def terminate():
        nonlocal requested
        requested = True
        task.cancel()

    try:
        try:
            loop.add_signal_handler(signal.SIGTERM, terminate)
            installed = True
        except (NotImplementedError, RuntimeError):
            pass
        try:
            return await task
        except asyncio.CancelledError:
            if not requested:
                raise
    finally:
        if installed:
            loop.remove_signal_handler(signal.SIGTERM)
