"""Fixtures for the application tests."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import sys
import weakref
from typing import TYPE_CHECKING, AsyncGenerator

import pytest

from ubo_app.utils.garbage_collection import examine

if TYPE_CHECKING:
    from _pytest.fixtures import SubRequest

    from ubo_app.menu import MenuApp

modules_snapshot = set(sys.modules)


class AppContext:
    """Context object for tests running a menu application."""

    def set_app(self: AppContext, app: MenuApp) -> None:
        """Set the application."""
        self.app = app
        loop = asyncio.get_event_loop()
        self.task = loop.create_task(self.app.async_run(async_lib='asyncio'))


@pytest.fixture()
async def app_context(request: SubRequest) -> AsyncGenerator[AppContext, None]:
    """Create the application."""
    import os

    os.environ['KIVY_NO_FILELOG'] = '1'
    os.environ['KIVY_NO_CONSOLELOG'] = '1'

    import headless_kivy_pi.config

    headless_kivy_pi.config.setup_headless_kivy({'automatic_fps': True})

    context = AppContext()

    yield context

    assert hasattr(context, 'task'), 'App not set for test'

    await context.task

    app_ref = weakref.ref(context.app)
    context.app.root.clear_widgets()

    del context.app
    del context.task

    gc.collect()
    app = app_ref()

    if app is not None and request.session.testsfailed == 0:
        logging.getLogger().debug(
            'Memory leak: failed to release app for test.\n'
            + json.dumps(
                {
                    'refcount': sys.getrefcount(app),
                    'referrers': gc.get_referrers(app),
                    'ref': app_ref,
                },
                sort_keys=True,
                indent=2,
                default=str,
            ),
        )
        gc.collect()
        for cell in gc.get_referrers(app):
            if type(cell).__name__ == 'cell':
                logging.getLogger().debug(
                    'CELL EXAMINATION\n' + json.dumps({'cell': cell}),
                )
                examine(cell, depth_limit=2)
        assert app is None, 'Memory leak: failed to release app for test'

    from kivy.core.window import Window

    Window.close()

    for module in set(sys.modules) - modules_snapshot:
        if module != 'objc' and 'numpy' not in module and 'cache' not in module:
            del sys.modules[module]
    gc.collect()
