# SPDX-FileCopyrightText: 2022-present deepset GmbH <info@deepset.ai>
#
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional, Union

from haystack.core.component import Component
from haystack.utils.asynchronous import is_callable_async_compatible


@dataclass
class ComponentInfo:
    """
    The `ComponentInfo` class encapsulates information about a component.

    :param type: The type of the component.
    :param name: The name of the component assigned when adding it to a pipeline.

    """

    type: str
    name: Optional[str] = field(default=None)

    @classmethod
    def from_component(cls, component: Component) -> "ComponentInfo":
        """
        Create a `ComponentInfo` object from a `Component` instance.

        :param component:
            The `Component` instance.
        :returns:
            The `ComponentInfo` object with the type and name of the given component.
        """
        component_type = f"{component.__class__.__module__}.{component.__class__.__name__}"
        component_name = getattr(component, "__component_name__", None)
        return cls(type=component_type, name=component_name)


@dataclass
class StreamingChunk:
    """
    The `StreamingChunk` class encapsulates a segment of streamed content along with associated metadata.

    This structure facilitates the handling and processing of streamed data in a systematic manner.

    :param content: The content of the message chunk as a string.
    :param meta: A dictionary containing metadata related to the message chunk.
    :param component_info: A `ComponentInfo` object containing information about the component that generated the chunk,
        such as the component name and type.
    """

    content: str
    meta: Dict[str, Any] = field(default_factory=dict, hash=False)
    component_info: Optional[ComponentInfo] = field(default=None, hash=False)


SyncStreamingCallbackT = Callable[[StreamingChunk], None]
AsyncStreamingCallbackT = Callable[[StreamingChunk], Awaitable[None]]

StreamingCallbackT = Union[SyncStreamingCallbackT, AsyncStreamingCallbackT]


def select_streaming_callback(
    init_callback: Optional[StreamingCallbackT], runtime_callback: Optional[StreamingCallbackT], requires_async: bool
) -> Optional[StreamingCallbackT]:
    """
    Picks the correct streaming callback given an optional initial and runtime callback.

    The runtime callback takes precedence over the initial callback.

    :param init_callback:
        The initial callback.
    :param runtime_callback:
        The runtime callback.
    :param requires_async:
        Whether the selected callback must be async compatible.
    :returns:
        The selected callback.
    """
    if init_callback is not None:
        if requires_async and not is_callable_async_compatible(init_callback):
            raise ValueError("The init callback must be async compatible.")
        if not requires_async and is_callable_async_compatible(init_callback):
            raise ValueError("The init callback cannot be a coroutine.")

    if runtime_callback is not None:
        if requires_async and not is_callable_async_compatible(runtime_callback):
            raise ValueError("The runtime callback must be async compatible.")
        if not requires_async and is_callable_async_compatible(runtime_callback):
            raise ValueError("The runtime callback cannot be a coroutine.")

    return runtime_callback or init_callback
