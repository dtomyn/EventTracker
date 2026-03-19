from __future__ import annotations

from contextlib import AbstractAsyncContextManager, AsyncExitStack
from functools import lru_cache
from typing import Callable, Protocol, TypeVar, cast


COPILOT_SDK_REQUIRED_MESSAGE = (
    "GitHub Copilot draft generation requires the github-copilot-sdk package."
)
COPILOT_CLIENT_SETTINGS_MESSAGE = (
    "Unable to initialize the GitHub Copilot client with the current settings. "
    "Most setups should leave COPILOT_CLI_PATH and COPILOT_CLI_URL unset."
)


class CopilotClientSettings(Protocol):
    @property
    def cli_path(self) -> str | None: ...

    @property
    def cli_url(self) -> str | None: ...


class CopilotSession(Protocol):
    async def send_and_wait(
        self, payload: dict[str, str], timeout: float
    ) -> object: ...

    def on(self, handler: Callable[[object], None]) -> Callable[[], None] | None: ...

    async def close(self) -> None: ...

    async def disconnect(self) -> None: ...


class CopilotClient(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def create_session(self, config: dict[str, object]) -> CopilotSession: ...


class CopilotClientConstructor(Protocol):
    def __call__(self, options: dict[str, str] | None = None) -> CopilotClient: ...


class CopilotPermissionHandler(Protocol):
    approve_all: object


class CopilotSdkModule(Protocol):
    CopilotClient: CopilotClientConstructor
    PermissionHandler: CopilotPermissionHandler


class SupportsAsyncStartStop(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class SupportsAsyncClose(Protocol):
    async def close(self) -> None: ...


class SupportsAsyncDisconnect(Protocol):
    async def disconnect(self) -> None: ...


TClient = TypeVar("TClient")
TResource = TypeVar("TResource")


@lru_cache(maxsize=1)
def load_copilot_sdk() -> CopilotSdkModule:
    from app.services import copilot_sdk

    return cast(CopilotSdkModule, copilot_sdk)


def instantiate_copilot_client(
    settings: CopilotClientSettings,
    *,
    configuration_error_type: type[Exception],
    missing_sdk_message: str,
    invalid_settings_message: str,
) -> CopilotClient:
    try:
        client_class = load_copilot_sdk().CopilotClient
    except ModuleNotFoundError as exc:
        if exc.name not in {"github_copilot_sdk", "copilot"}:
            raise
        raise configuration_error_type(missing_sdk_message) from exc

    options = {
        "cli_path": settings.cli_path,
        "cli_url": settings.cli_url,
    }
    filtered_options = {
        key: value for key, value in options.items() if value is not None
    }
    try:
        return client_class(filtered_options or None)
    except (TypeError, ValueError) as exc:
        raise configuration_error_type(invalid_settings_message) from exc


def get_permission_handler() -> object:
    return load_copilot_sdk().PermissionHandler.approve_all


async def create_copilot_session(
    client: CopilotClient,
    *,
    model_id: str,
    system_message: str,
    reasoning_effort: str | None = None,
    streaming: bool = False,
) -> CopilotSession:
    config: dict[str, object] = {
        "model": model_id,
        "on_permission_request": get_permission_handler(),
        "system_message": {
            "mode": "append",
            "content": system_message,
        },
    }
    if reasoning_effort is not None:
        config["reasoning_effort"] = reasoning_effort
    if streaming:
        config["streaming"] = True
    return await client.create_session(config)


async def prepare_copilot_client(
    exit_stack: AsyncExitStack,
    client: TClient,
) -> TClient:
    enter_async = getattr(client, "__aenter__", None)
    exit_async = getattr(client, "__aexit__", None)
    if callable(enter_async) and callable(exit_async):
        context_manager = cast(AbstractAsyncContextManager[TClient], client)
        return await exit_stack.enter_async_context(context_manager)

    start = getattr(client, "start", None)
    stop = getattr(client, "stop", None)
    if callable(start) and callable(stop):
        lifecycle_client = cast(SupportsAsyncStartStop, client)
        await lifecycle_client.start()
        exit_stack.push_async_callback(lifecycle_client.stop)
        return client

    raise TypeError(
        "Copilot client does not support async context management or start/stop lifecycle."
    )


async def prepare_copilot_resource(
    exit_stack: AsyncExitStack,
    resource: TResource,
) -> TResource:
    close = getattr(resource, "close", None)
    if callable(close):
        closeable_resource = cast(SupportsAsyncClose, resource)
        exit_stack.push_async_callback(closeable_resource.close)
        return resource

    disconnect = getattr(resource, "disconnect", None)
    if callable(disconnect):
        disconnectable_resource = cast(SupportsAsyncDisconnect, resource)
        exit_stack.push_async_callback(disconnectable_resource.disconnect)
        return resource

    raise TypeError(
        "Copilot resource does not support close or disconnect lifecycle methods."
    )


async def send_copilot_prompt(
    session: CopilotSession, prompt: str, *, timeout: float
) -> object:
    return await session.send_and_wait({"prompt": prompt}, timeout=timeout)


def extract_copilot_message_content(response: object) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("content", "text", "message", "response", "output", "data"):
            extracted = extract_copilot_message_content(response.get(key))
            if extracted:
                return extracted
        for key in ("messages", "events", "items"):
            items = response.get(key)
            if isinstance(items, list):
                for item in reversed(items):
                    extracted = extract_copilot_message_content(item)
                    if extracted:
                        return extracted
        return ""
    if isinstance(response, (list, tuple)):
        for item in reversed(response):
            extracted = extract_copilot_message_content(item)
            if extracted:
                return extracted
        return ""

    for attr_name in ("content", "text", "message", "response", "output", "data"):
        if hasattr(response, attr_name):
            extracted = extract_copilot_message_content(getattr(response, attr_name))
            if extracted:
                return extracted
    return ""


def subscribe_to_session_events(
    session: CopilotSession,
    handler: Callable[[object], None] | None,
) -> Callable[[], None]:
    if handler is None:
        return lambda: None

    try:
        on_method = session.on
    except AttributeError:
        return lambda: None

    unsubscribe = on_method(handler)
    if callable(unsubscribe):
        return unsubscribe
    return lambda: None
