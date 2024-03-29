"""Docker image menu."""

from __future__ import annotations

import contextlib
import pathlib
from typing import Callable

import docker
import docker.errors
from docker.models.containers import Container
from docker.models.images import Image
from kivy.lang.builder import Builder
from kivy.properties import ListProperty, NumericProperty, StringProperty
from reducer import IMAGE_IDS, IMAGES
from ubo_gui.menu.types import ActionItem, HeadedMenu, HeadlessMenu, Item, SubMenuItem
from ubo_gui.page import PageWidget

from ubo_app.logging import logger
from ubo_app.store import autorun, dispatch
from ubo_app.store.services.docker import (
    DockerImageSetDockerIdAction,
    DockerImageSetStatusAction,
    DockerState,
    ImageState,
    ImageStatus,
)
from ubo_app.store.services.notifications import (
    Importance,
    Notification,
    NotificationsAddAction,
)
from ubo_app.utils.async_ import create_task, run_in_executor


def find_container(client: docker.DockerClient, *, image: str) -> Container | None:
    """Find a container."""
    for container in client.containers.list(all=True):
        if not isinstance(container, Container):
            continue

        with contextlib.suppress(docker.errors.DockerException):
            container_image = container.image
            if isinstance(container_image, Image) and image in container_image.tags:
                return container
    return None


def update_container(image_id: str, container: Container) -> None:
    """Update a container's state in store based on its real state."""
    if container.status == 'running':
        logger.debug(
            'Container running image found',
            extra={'image': image_id, 'path': IMAGES[image_id].path},
        )
        dispatch(
            DockerImageSetStatusAction(
                image=image_id,
                status=ImageStatus.RUNNING,
                ports=[
                    f'{i["HostIp"]}:{i["HostPort"]}'
                    for i in container.ports.values()
                    for i in i
                ],
                ip=container.attrs['NetworkSettings']['Networks']['bridge']['IPAddress']
                if container.attrs
                else None,
            ),
        )
        return
    logger.debug(
        "Container for the image found, but it's not running",
        extra={'image': image_id, 'path': IMAGES[image_id].path},
    )
    dispatch(
        DockerImageSetStatusAction(
            image=image_id,
            status=ImageStatus.CREATED,
        ),
    )


def _monitor_events(  # noqa: C901
    image_id: str,
    get_docker_id: Callable[[], str],
    docker_client: docker.DockerClient,
) -> None:
    path = IMAGES[image_id].path
    events = docker_client.events(
        decode=True,
        filters={'type': ['image', 'container']},
    )
    for event in events:
        logger.verbose('Docker image event', extra={'event': event})
        if event['Type'] == 'image':
            if event['status'] == 'pull' and event['id'] == path:
                try:
                    image = docker_client.images.get(path)
                    dispatch(
                        DockerImageSetStatusAction(
                            image=image_id,
                            status=ImageStatus.AVAILABLE,
                        ),
                    )
                    if isinstance(image, Image) and image.id:
                        dispatch(
                            DockerImageSetDockerIdAction(
                                image=image_id,
                                docker_id=image.id,
                            ),
                        )
                except docker.errors.DockerException:
                    dispatch(
                        DockerImageSetStatusAction(
                            image=image_id,
                            status=ImageStatus.NOT_AVAILABLE,
                        ),
                    )
            elif event['status'] == 'delete' and event['id'] == get_docker_id():
                dispatch(
                    DockerImageSetStatusAction(
                        image=image_id,
                        status=ImageStatus.NOT_AVAILABLE,
                    ),
                )
        elif event['Type'] == 'container':
            if event['status'] == 'start' and event['from'] == path:
                container = find_container(docker_client, image=path)
                if container:
                    update_container(image_id, container)
            elif event['status'] == 'die' and event['from'] == path:
                dispatch(
                    DockerImageSetStatusAction(
                        image=image_id,
                        status=ImageStatus.CREATED,
                    ),
                )
            elif event['status'] == 'destroy' and event['from'] == path:
                dispatch(
                    DockerImageSetStatusAction(
                        image=image_id,
                        status=ImageStatus.AVAILABLE,
                    ),
                )


def check_container(image_id: str) -> None:
    """Check the container status."""
    path = IMAGES[image_id].path

    async def act() -> None:
        logger.debug('Checking image', extra={'image': image_id, 'path': path})
        docker_client = docker.from_env()
        try:
            image = docker_client.images.get(path)
            if not isinstance(image, Image):
                raise docker.errors.ImageNotFound(path)  # noqa: TRY301

            if image.id:
                dispatch(
                    DockerImageSetDockerIdAction(
                        image=image_id,
                        docker_id=image.id,
                    ),
                )
            logger.debug('Image found', extra={'image': image_id, 'path': path})

            container = find_container(docker_client, image=path)
            if container:
                update_container(image_id, container)
                return

            logger.debug(
                'Container running image not found',
                extra={'image': image_id, 'path': path},
            )
            dispatch(
                DockerImageSetStatusAction(
                    image=image_id,
                    status=ImageStatus.AVAILABLE,
                ),
            )
        except docker.errors.ImageNotFound as exception:
            logger.debug(
                'Image not found',
                extra={'image': image_id, 'path': path},
                exc_info=exception,
            )
            dispatch(
                DockerImageSetStatusAction(
                    image=image_id,
                    status=ImageStatus.NOT_AVAILABLE,
                ),
            )
        except docker.errors.DockerException as exception:
            logger.debug(
                'Image error',
                extra={'image': image_id, 'path': path},
                exc_info=exception,
            )
            dispatch(
                DockerImageSetStatusAction(
                    image=image_id,
                    status=ImageStatus.ERROR,
                ),
            )
        finally:

            @autorun(lambda state: getattr(state.docker, image_id).docker_id)
            def get_docker_id(docker_id: str) -> str:
                return docker_id

            _monitor_events(image_id, get_docker_id, docker_client)
            docker_client.close()

    create_task(act())


def _fetch_image(image: ImageState) -> None:
    def act() -> None:
        dispatch(
            DockerImageSetStatusAction(
                image=image.id,
                status=ImageStatus.FETCHING,
            ),
        )
        try:
            logger.debug('Fetching image', extra={'image': image.path})
            docker_client = docker.from_env()
            response = docker_client.api.pull(
                image.path,
                stream=True,
                decode=True,
            )
            for line in response:
                dispatch(
                    DockerImageSetStatusAction(
                        image=image.id,
                        status=ImageStatus.FETCHING,
                    ),
                )
                logger.verbose(
                    'Image pull',
                    extra={'image': image.path, 'line': line},
                )
            logger.debug('Image fetched', extra={'image': image.path})
            docker_client.close()
        except docker.errors.DockerException as exception:
            logger.debug(
                'Image error',
                extra={'image': image.path},
                exc_info=exception,
            )
            dispatch(
                DockerImageSetStatusAction(
                    image=image.id,
                    status=ImageStatus.ERROR,
                ),
            )

    run_in_executor(None, act)


def _remove_image(image: ImageState) -> None:
    def act() -> None:
        docker_client = docker.from_env()
        docker_client.images.remove(image.path, force=True)
        docker_client.close()

    run_in_executor(None, act)


@autorun(lambda state: state.docker)
def _run_container_generator(docker_state: DockerState) -> Callable[[ImageState], None]:
    def run_container(image: ImageState) -> None:
        def act() -> None:
            docker_client = docker.from_env()
            container = find_container(docker_client, image=image.path)
            if container:
                if container.status != 'running':
                    container.start()
            else:
                hosts = {}
                for key, value in IMAGES[image.id].hosts.items():
                    if not hasattr(docker_state, value):
                        dispatch(
                            NotificationsAddAction(
                                notification=Notification(
                                    title='Dependency error',
                                    content=f'Container "{value}" is not loaded',
                                    importance=Importance.MEDIUM,
                                ),
                            ),
                        )
                        return
                    if not getattr(docker_state, value).container_ip:
                        dispatch(
                            NotificationsAddAction(
                                notification=Notification(
                                    title='Dependency error',
                                    content=f'Container "{value}" does not have an IP'
                                    ' address',
                                    importance=Importance.MEDIUM,
                                ),
                            ),
                        )
                        return
                    if hasattr(docker_state, value):
                        hosts[key] = getattr(docker_state, value).container_ip
                    else:
                        hosts[key] = value
                docker_client.containers.run(
                    image.path,
                    hostname=image.id,
                    publish_all_ports=True,
                    detach=True,
                    volumes=IMAGES[image.id].volumes,
                    ports=IMAGES[image.id].ports,
                    network_mode=IMAGES[image.id].network_mode,
                    environment=IMAGES[image.id].environment,
                    extra_hosts=hosts,
                    restart_policy='always',
                )
            docker_client.close()

        run_in_executor(None, act)

    return run_container


def _stop_container(image: ImageState) -> None:
    def act() -> None:
        docker_client = docker.from_env()
        container = find_container(docker_client, image=image.path)
        if container and container.status != 'exited':
            container.stop()
        docker_client.close()

    run_in_executor(None, act)


def _remove_container(image: ImageState) -> None:
    def act() -> None:
        docker_client = docker.from_env()
        container = find_container(docker_client, image=image.path)
        if container:
            container.remove(v=True, force=True)
        docker_client.close()

    run_in_executor(None, act)


class DockerQRCode(PageWidget):
    """QR code for the container's url (ip and port)."""

    ips: list[str] = ListProperty()
    port: str = StringProperty()
    index: int = NumericProperty(0)

    def go_down(self: DockerQRCode) -> None:
        """Go down."""
        self.index = (self.index + 1) % len(self.ips)
        self.ids.slider.animated_value = len(self.ips) - 1 - self.index

    def go_up(self: DockerQRCode) -> None:
        """Go up."""
        self.index = (self.index - 1) % len(self.ips)
        self.ids.slider.animated_value = len(self.ips) - 1 - self.index


def image_menu(
    image: ImageState,
) -> HeadedMenu:
    """Get the menu for the docker image."""
    items: list[Item] = []

    def open_qrcode(port: str) -> Callable[[], PageWidget]:
        def action() -> PageWidget:
            return DockerQRCode(ips=image.ip_addresses, port=port)

        return action

    if image.status == ImageStatus.NOT_AVAILABLE:
        items.append(
            ActionItem(
                label='Fetch',
                icon='󰇚',
                action=lambda: _fetch_image(image),
            ),
        )
    elif image.status == ImageStatus.FETCHING:
        items.append(
            ActionItem(
                label='Stop',
                icon='󰓛',
                action=lambda: _remove_image(image),
            ),
        )
    elif image.status == ImageStatus.AVAILABLE:
        items.extend(
            [
                ActionItem(
                    label='Start',
                    icon='󰐊',
                    action=lambda: _run_container_generator()(image),
                ),
                ActionItem(
                    label='Remove image',
                    icon='󰆴',
                    action=lambda: _remove_image(image),
                ),
            ],
        )
    elif image.status == ImageStatus.CREATED:
        items.extend(
            [
                ActionItem(
                    label='Start',
                    icon='󰐊',
                    action=lambda: _run_container_generator()(image),
                ),
                ActionItem(
                    label='Remove container',
                    icon='󰆴',
                    action=lambda: _remove_container(image),
                ),
            ],
        )
    elif image.status == ImageStatus.RUNNING:
        items.append(
            ActionItem(
                label='Stop',
                icon='󰓛',
                action=lambda: _stop_container(image),
            ),
        )
        items.append(
            SubMenuItem(
                label='Ports',
                icon='󰙜',
                sub_menu=HeadlessMenu(
                    title='Ports',
                    items=[
                        ActionItem(
                            label=port,
                            icon='󰙜',
                            action=open_qrcode(port.split(':')[-1]),
                        )
                        if port.startswith('0.0.0.0')  # noqa: S104
                        else Item(label=port, icon='󰙜')
                        for port in image.ports
                    ],
                    placeholder='No ports',
                ),
            ),
        )

    return HeadedMenu(
        title=f'Docker - {image.label}',
        heading=image.label,
        sub_heading={
            ImageStatus.NOT_AVAILABLE: 'Image needs to be fetched',
            ImageStatus.FETCHING: 'Image is being fetched',
            ImageStatus.AVAILABLE: 'Image is ready but container is not running',
            ImageStatus.CREATED: 'Container is created but not running',
            ImageStatus.RUNNING: 'Container is running',
            ImageStatus.ERROR: 'Image has an error, please check the logs',
        }[image.status],
        items=items,
    )


def image_menu_generator(image_id: str) -> Callable[[], Callable[[], HeadedMenu]]:
    """Get the menu items for the Docker service."""
    _image_menu = autorun(lambda state: getattr(state.docker, image_id))(image_menu)

    def image_action() -> Callable[[], HeadedMenu]:
        """Get the menu items for the Docker service."""
        check_container(image_id)

        return _image_menu

    return image_action


image_menus = {image_id: image_menu_generator(image_id) for image_id in IMAGE_IDS}
Builder.load_file(
    pathlib.Path(__file__).parent.joinpath('docker_qrcode.kv').resolve().as_posix(),
)
