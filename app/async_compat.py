from __future__ import annotations

import asyncio
from functools import partial
from typing import Any, Callable


async def run_in_thread(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Uruchamia blokującą funkcję poza pętlą także na Pythonie 3.8."""

    native = getattr(asyncio, "to_thread", None)
    if callable(native):
        return await native(function, *args, **kwargs)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(function, *args, **kwargs))
