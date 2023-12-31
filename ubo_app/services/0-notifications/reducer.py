# ruff: noqa: D100, D101, D102, D103, D104, D107, N999
from __future__ import annotations

from dataclasses import replace

from redux import (
    BaseEvent,
    CompleteReducerResult,
    InitAction,
    InitializationActionError,
    ReducerResult,
)

from ubo_app.store.notifications import (
    NotificationDisplayType,
    NotificationsAction,
    NotificationsAddAction,
    NotificationsClearAction,
    NotificationsClearAllAction,
    NotificationsDisplayEvent,
    NotificationsState,
)

Action = InitAction | NotificationsAction


def reducer(
    state: NotificationsState | None,
    action: Action,
) -> ReducerResult[NotificationsState, Action, BaseEvent]:
    if state is None:
        if isinstance(action, InitAction):
            return NotificationsState(
                notifications=[],
                unread_count=0,
            )
        raise InitializationActionError(action)

    if isinstance(action, NotificationsAddAction):
        events = []
        if action.notification.display_type in (
            NotificationDisplayType.FLASH,
            NotificationDisplayType.STICKY,
        ):
            events.append(NotificationsDisplayEvent(notification=action.notification))
        if action.notification in state.notifications:
            return CompleteReducerResult(state=state, events=events)
        return CompleteReducerResult(
            state=replace(
                state,
                notifications=[*state.notifications, action.notification],
                unread_count=state.unread_count + 1,
            ),
            events=events,
        )
    if isinstance(action, NotificationsClearAction):
        return replace(
            state,
            notifications=[
                notification
                for notification in state.notifications
                if notification is not action.notification
            ],
            unread_count=state.unread_count - 1
            if action.notification in state.notifications
            else state.unread_count,
        )
    if isinstance(action, NotificationsClearAllAction):
        return replace(state, notifications=[], unread_count=0)
    return state
