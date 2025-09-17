"""Pytest configuration shared across the test suite."""

from __future__ import annotations

import asyncio
import inspect
import pathlib
import sys
from collections.abc import Generator

import pytest


# Ensure repo root is on sys.path so tests can import the local package.
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Provide a fresh event loop for tests that require one.

    Pytest itself does not know how to work with ``async def`` tests unless an
    asyncio plugin is installed.  Some of our tests rely on that behaviour, so
    we provide a tiny shim that mimics the part of ``pytest-asyncio`` we need.
    """

    try:
        previous_loop = asyncio.get_event_loop()
    except RuntimeError:
        previous_loop = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        asyncio.set_event_loop(previous_loop)
        loop.close()


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Execute ``async def`` tests using the provided event loop.

    This mirrors the minimal behaviour offered by ``pytest-asyncio`` so the
    suite can run without needing the third-party plugin installed.
    """

    test_function = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_function):
        return None

    loop = pyfuncitem.funcargs.get("event_loop")
    if loop is None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(test_function(**pyfuncitem.funcargs))
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()
    else:
        loop.run_until_complete(test_function(**pyfuncitem.funcargs))
    return True


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register options used for compatibility with ``pytest-asyncio``."""

    parser.addini(
        "asyncio_default_fixture_loop_scope",
        "Compatibility shim so pytest recognises our asyncio configuration.",
        default="function",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Ensure the ``asyncio`` marker is recognised to avoid warnings."""

    config.addinivalue_line(
        "markers",
        "asyncio: mark a test as using asyncio. Provided for compatibility.",
    )
