# ruff: noqa: D100, D101, D102, D103, D104, D107
from __future__ import annotations

import asyncio
import pathlib
import threading
from functools import cached_property
from threading import Thread
from typing import TYPE_CHECKING, Self, Sequence

from debouncer import DebounceOptions, debounce
from kivy.app import Builder
from kivy.clock import Clock
from redux import EventSubscriptionOptions, FinishEvent
from ubo_gui.app import UboApp
from ubo_gui.gauge import GaugeWidget
from ubo_gui.menu import MenuWidget
from ubo_gui.notification import NotificationWidget
from ubo_gui.page import PageWidget
from ubo_gui.volume import VolumeWidget

from ubo_app.constants import DEBUG_MODE
from ubo_app.store.keypad import Key, KeypadKeyPressEvent
from ubo_app.store.main import SetMenuPathAction
from ubo_app.store.notifications import (
    NotificationDisplayType,
    NotificationsClearAction,
    NotificationsDisplayEvent,
)

from .store import autorun, dispatch, subscribe_event

if TYPE_CHECKING:
    from kivy.uix.screenmanager import Screen
    from kivy.uix.widget import Widget
    from ubo_gui.menu.types import Item, Menu


class HomePage(PageWidget):
    def __init__(
        self: Self,
        items: Sequence[Item] | None = None,
        *args: object,
        **kwargs: object,
    ) -> None:
        super().__init__(items, *args, **kwargs)

        self.ids.central_column.add_widget(self.cpu_gauge)
        self.ids.central_column.add_widget(self.ram_gauge)

        volume_widget = VolumeWidget()
        self.ids.right_column.add_widget(volume_widget)

        @autorun(lambda state: state.sound.output_volume)
        def sync_output_volume(selector_result: float) -> None:
            volume_widget.value = selector_result * 100

    @cached_property
    def cpu_gauge(self: Self) -> GaugeWidget:
        import psutil

        gauge = GaugeWidget(value=0, fill_color='#24D636', label='CPU')

        value = 0

        def set_value(_: float) -> None:
            gauge.value = value

        def calculate_value() -> None:
            nonlocal value
            value = psutil.cpu_percent(interval=1, percpu=False)
            Clock.schedule_once(set_value)

        Clock.schedule_interval(
            lambda _: Thread(target=calculate_value).start(),
            1,
        )

        return gauge

    @cached_property
    def ram_gauge(self: Self) -> GaugeWidget:
        import psutil

        gauge = GaugeWidget(
            value=psutil.virtual_memory().percent,
            fill_color='#D68F24',
            label='RAM',
        )

        def set_value(_: int) -> None:
            gauge.value = psutil.virtual_memory().percent

        Clock.schedule_interval(set_value, 1)

        return gauge


class MenuWidgetWithHomePage(MenuWidget):
    def get_current_screen(self: Self) -> Screen | None:
        if self.depth == 0:
            return HomePage(
                self.current_menu_items,
                name=f'Page {self.get_depth()} 0',
            )
        return super().get_current_screen()


def set_path(_: MenuWidget, stack: list[tuple[Menu, int] | PageWidget]) -> None:
    dispatch(
        SetMenuPathAction(
            path=[
                i.name
                if isinstance(i, PageWidget)
                else i[0].title()
                if callable(i[0].title)
                else i[0].title
                for i in stack
            ],
        ),
    )


class WorkerThread(threading.Thread):
    def __init__(self: WorkerThread) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        if DEBUG_MODE:
            self.loop.set_debug(enabled=True)

    def run(self: WorkerThread) -> None:
        asyncio.set_event_loop(self.loop)

        subscribe_event(FinishEvent, lambda _: self.stop())
        self.loop.run_forever()

    def stop(self: WorkerThread) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)


class MenuAppCentral(UboApp):
    @cached_property
    def central(self: MenuAppCentral) -> Widget | None:
        """Build the main menu and initiate it."""
        menu_widget = MenuWidgetWithHomePage()

        @autorun(lambda state: state.main.menu)
        @debounce(0.1, DebounceOptions(leading=True, trailing=True, time_window=0.1))
        async def sync_current_menu(menu: Menu | None) -> None:
            if not menu:
                return
            Clock.schedule_once(lambda _: menu_widget.set_root_menu(menu))

        thread = WorkerThread()
        thread.start()
        sync_current_menu.subscribe(
            lambda q: thread.loop.call_soon_threadsafe(
                lambda: thread.loop.create_task(q),
            ),
        )

        def handle_title_change(_: MenuWidget, title: str) -> None:
            self.root.title = title

        self.root.title = menu_widget.title
        menu_widget.bind(title=handle_title_change)

        menu_widget.bind(stack=set_path)

        def handle_key_press_event(key_press_event: KeypadKeyPressEvent) -> None:
            if key_press_event.key == Key.L1:
                menu_widget.select(0)
            if key_press_event.key == Key.L2:
                menu_widget.select(1)
            if key_press_event.key == Key.L3:
                menu_widget.select(2)
            if key_press_event.key == Key.BACK:
                menu_widget.go_back()
            if key_press_event.key == Key.UP:
                menu_widget.go_up()
            if key_press_event.key == Key.DOWN:
                menu_widget.go_down()

        subscribe_event(
            KeypadKeyPressEvent,
            handle_key_press_event,
            EventSubscriptionOptions(run_async=False),
        )

        def display_notification(event: NotificationsDisplayEvent) -> None:
            notification = event.notification
            application = NotificationWidget(
                title='Notification',
                notification_title=notification.title,
                content=notification.content,
                icon=notification.icon,
                color=notification.color,
            )

            application.bind(
                on_dismiss=lambda _: (
                    application.dispatch('on_close'),
                    dispatch(
                        NotificationsClearAction(notification=notification),
                    ),
                ),
            )

            menu_widget.open_application(application)

            if notification.display_type is NotificationDisplayType.FLASH:
                Clock.schedule_once(lambda _: application.dispatch('on_close'), 4)

        subscribe_event(
            NotificationsDisplayEvent,
            lambda event: Clock.schedule_once(
                lambda _: display_notification(event),
                -1,
            ),
        )

        return menu_widget


Builder.load_file(
    pathlib.Path(__file__).parent.joinpath('home_page.kv').resolve().as_posix(),
)
