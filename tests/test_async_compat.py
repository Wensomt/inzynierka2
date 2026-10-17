import asyncio

from app.async_compat import run_in_thread


def test_run_in_thread_passes_arguments_and_keywords() -> None:
    def combine(left: int, right: int = 0) -> int:
        return left + right

    assert asyncio.run(run_in_thread(combine, 4, right=7)) == 11


def test_run_in_thread_uses_executor_when_native_helper_is_absent(monkeypatch) -> None:
    monkeypatch.delattr(asyncio, "to_thread", raising=False)

    assert asyncio.run(run_in_thread(lambda value: value * 3, 5)) == 15
