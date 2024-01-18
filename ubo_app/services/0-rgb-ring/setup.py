# ruff: noqa: D100, D101, D102, D103, D104, D107, N999
from ubo_app.store import dispatch, subscribe_event
from ubo_app.store.rgb_ring import (
    RgbRingCommandEvent,
    RgbRingPulseAction,
    RgbRingSetBrightnessAction,
)


def init_service() -> None:
    from rgb_ring_client import RgbRingClient

    rgb_ring_client = RgbRingClient()

    def handle_rgb_ring_command(event: RgbRingCommandEvent) -> None:
        rgb_ring_client.send(event.command)

    subscribe_event(RgbRingCommandEvent, handle_rgb_ring_command)

    dispatch(RgbRingSetBrightnessAction(brightness=0.2))
    dispatch(RgbRingPulseAction(repetitions=2, wait=12))