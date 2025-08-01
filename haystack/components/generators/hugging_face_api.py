# SPDX-FileCopyrightText: 2022-present deepset GmbH <info@deepset.ai>
#
# SPDX-License-Identifier: Apache-2.0

from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Union, cast

from haystack import component, default_from_dict, default_to_dict, logging
from haystack.dataclasses import (
    ComponentInfo,
    FinishReason,
    StreamingCallbackT,
    StreamingChunk,
    SyncStreamingCallbackT,
    select_streaming_callback,
)
from haystack.lazy_imports import LazyImport
from haystack.utils import Secret, deserialize_callable, deserialize_secrets_inplace, serialize_callable
from haystack.utils.hf import HFGenerationAPIType, HFModelType, check_valid_model
from haystack.utils.url_validation import is_valid_http_url

with LazyImport(message="Run 'pip install \"huggingface_hub>=0.27.0\"'") as huggingface_hub_import:
    from huggingface_hub import (
        InferenceClient,
        TextGenerationOutput,
        TextGenerationStreamOutput,
        TextGenerationStreamOutputToken,
    )


logger = logging.getLogger(__name__)


@component
class HuggingFaceAPIGenerator:
    """
    Generates text using Hugging Face APIs.

    Use it with the following Hugging Face APIs:
    - [Paid Inference Endpoints](https://huggingface.co/inference-endpoints)
    - [Self-hosted Text Generation Inference](https://github.com/huggingface/text-generation-inference)

    **Note:** As of July 2025, the Hugging Face Inference API no longer offers generative models through the
    `text_generation` endpoint. Generative models are now only available through providers supporting the
    `chat_completion` endpoint. As a result, this component might no longer work with the Hugging Face Inference API.
    Use the `HuggingFaceAPIChatGenerator` component, which supports the `chat_completion` endpoint.

    ### Usage examples

    #### With Hugging Face Inference Endpoints

    ```python
    from haystack.components.generators import HuggingFaceAPIGenerator
    from haystack.utils import Secret

    generator = HuggingFaceAPIGenerator(api_type="inference_endpoints",
                                        api_params={"url": "<your-inference-endpoint-url>"},
                                        token=Secret.from_token("<your-api-key>"))

    result = generator.run(prompt="What's Natural Language Processing?")
    print(result)
    ```

    #### With self-hosted text generation inference
    ```python
    from haystack.components.generators import HuggingFaceAPIGenerator

    generator = HuggingFaceAPIGenerator(api_type="text_generation_inference",
                                        api_params={"url": "http://localhost:8080"})

    result = generator.run(prompt="What's Natural Language Processing?")
    print(result)
    ```

    #### With the free serverless inference API

    Be aware that this example might not work as the Hugging Face Inference API no longer offer models that support the
    `text_generation` endpoint. Use the `HuggingFaceAPIChatGenerator` for generative models through the
    `chat_completion` endpoint.

    ```python
    from haystack.components.generators import HuggingFaceAPIGenerator
    from haystack.utils import Secret

    generator = HuggingFaceAPIGenerator(api_type="serverless_inference_api",
                                        api_params={"model": "HuggingFaceH4/zephyr-7b-beta"},
                                        token=Secret.from_token("<your-api-key>"))

    result = generator.run(prompt="What's Natural Language Processing?")
    print(result)
    ```
    """

    def __init__(  # pylint: disable=too-many-positional-arguments
        self,
        api_type: Union[HFGenerationAPIType, str],
        api_params: Dict[str, str],
        token: Optional[Secret] = Secret.from_env_var(["HF_API_TOKEN", "HF_TOKEN"], strict=False),
        generation_kwargs: Optional[Dict[str, Any]] = None,
        stop_words: Optional[List[str]] = None,
        streaming_callback: Optional[StreamingCallbackT] = None,
    ):
        """
        Initialize the HuggingFaceAPIGenerator instance.

        :param api_type:
            The type of Hugging Face API to use. Available types:
            - `text_generation_inference`: See [TGI](https://github.com/huggingface/text-generation-inference).
            - `inference_endpoints`: See [Inference Endpoints](https://huggingface.co/inference-endpoints).
            - `serverless_inference_api`: See [Serverless Inference API](https://huggingface.co/inference-api).
              This might no longer work due to changes in the models offered in the Hugging Face Inference API.
              Please use the `HuggingFaceAPIChatGenerator` component instead.
        :param api_params:
            A dictionary with the following keys:
            - `model`: Hugging Face model ID. Required when `api_type` is `SERVERLESS_INFERENCE_API`.
            - `url`: URL of the inference endpoint. Required when `api_type` is `INFERENCE_ENDPOINTS` or
            `TEXT_GENERATION_INFERENCE`.
            - Other parameters specific to the chosen API type, such as `timeout`, `headers`, `provider` etc.
        :param token: The Hugging Face token to use as HTTP bearer authorization.
            Check your HF token in your [account settings](https://huggingface.co/settings/tokens).
        :param generation_kwargs:
            A dictionary with keyword arguments to customize text generation. Some examples: `max_new_tokens`,
            `temperature`, `top_k`, `top_p`.
            For details, see [Hugging Face documentation](https://huggingface.co/docs/huggingface_hub/en/package_reference/inference_client#huggingface_hub.InferenceClient.text_generation)
            for more information.
        :param stop_words: An optional list of strings representing the stop words.
        :param streaming_callback: An optional callable for handling streaming responses.
        """

        huggingface_hub_import.check()

        if isinstance(api_type, str):
            api_type = HFGenerationAPIType.from_str(api_type)

        if api_type == HFGenerationAPIType.SERVERLESS_INFERENCE_API:
            logger.warning(
                "Due to changes in the models offered in Hugging Face Inference API, using this component with the "
                "Serverless Inference API might no longer work. "
                "Please use the `HuggingFaceAPIChatGenerator` component instead."
            )
            model = api_params.get("model")
            if model is None:
                raise ValueError(
                    "To use the Serverless Inference API, you need to specify the `model` parameter in `api_params`."
                )
            check_valid_model(model, HFModelType.GENERATION, token)
            model_or_url = model
        elif api_type in [HFGenerationAPIType.INFERENCE_ENDPOINTS, HFGenerationAPIType.TEXT_GENERATION_INFERENCE]:
            url = api_params.get("url")
            if url is None:
                msg = (
                    "To use Text Generation Inference or Inference Endpoints, you need to specify the `url` "
                    "parameter in `api_params`."
                )
                raise ValueError(msg)
            if not is_valid_http_url(url):
                raise ValueError(f"Invalid URL: {url}")
            model_or_url = url
        else:
            msg = f"Unknown api_type {api_type}"
            raise ValueError(msg)

        # handle generation kwargs setup
        generation_kwargs = generation_kwargs.copy() if generation_kwargs else {}
        generation_kwargs["stop_sequences"] = generation_kwargs.get("stop_sequences", [])
        generation_kwargs["stop_sequences"].extend(stop_words or [])
        generation_kwargs.setdefault("max_new_tokens", 512)

        self.api_type = api_type
        self.api_params = api_params
        self.token = token
        self.generation_kwargs = generation_kwargs
        self.streaming_callback = streaming_callback

        resolved_api_params: Dict[str, Any] = {k: v for k, v in api_params.items() if k != "model" and k != "url"}
        self._client = InferenceClient(
            model_or_url, token=token.resolve_value() if token else None, **resolved_api_params
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize this component to a dictionary.

        :returns:
            A dictionary containing the serialized component.
        """
        callback_name = serialize_callable(self.streaming_callback) if self.streaming_callback else None
        return default_to_dict(
            self,
            api_type=str(self.api_type),
            api_params=self.api_params,
            token=self.token.to_dict() if self.token else None,
            generation_kwargs=self.generation_kwargs,
            streaming_callback=callback_name,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HuggingFaceAPIGenerator":
        """
        Deserialize this component from a dictionary.
        """
        deserialize_secrets_inplace(data["init_parameters"], keys=["token"])
        init_params = data["init_parameters"]
        serialized_callback_handler = init_params.get("streaming_callback")
        if serialized_callback_handler:
            init_params["streaming_callback"] = deserialize_callable(serialized_callback_handler)
        return default_from_dict(cls, data)

    @component.output_types(replies=List[str], meta=List[Dict[str, Any]])
    def run(
        self,
        prompt: str,
        streaming_callback: Optional[StreamingCallbackT] = None,
        generation_kwargs: Optional[Dict[str, Any]] = None,
    ):
        """
        Invoke the text generation inference for the given prompt and generation parameters.

        :param prompt:
            A string representing the prompt.
        :param streaming_callback:
            A callback function that is called when a new token is received from the stream.
        :param generation_kwargs:
            Additional keyword arguments for text generation.
        :returns:
            A dictionary with the generated replies and metadata. Both are lists of length n.
            - replies: A list of strings representing the generated replies.
        """
        # update generation kwargs by merging with the default ones
        generation_kwargs = {**self.generation_kwargs, **(generation_kwargs or {})}

        # check if streaming_callback is passed
        streaming_callback = select_streaming_callback(
            init_callback=self.streaming_callback, runtime_callback=streaming_callback, requires_async=False
        )

        hf_output = self._client.text_generation(
            prompt, details=True, stream=streaming_callback is not None, **generation_kwargs
        )

        if streaming_callback is not None:
            # mypy doesn't know that hf_output is a Iterable[TextGenerationStreamOutput], so we cast it
            return self._stream_and_build_response(
                hf_output=cast(Iterable[TextGenerationStreamOutput], hf_output), streaming_callback=streaming_callback
            )

        # mypy doesn't know that hf_output is a TextGenerationOutput, so we cast it
        return self._build_non_streaming_response(cast(TextGenerationOutput, hf_output))

    def _stream_and_build_response(
        self, hf_output: Iterable["TextGenerationStreamOutput"], streaming_callback: SyncStreamingCallbackT
    ):
        chunks: List[StreamingChunk] = []
        first_chunk_time = None

        component_info = ComponentInfo.from_component(self)
        for chunk in hf_output:
            token: TextGenerationStreamOutputToken = chunk.token
            if token.special:
                continue

            chunk_metadata = {**asdict(token), **(asdict(chunk.details) if chunk.details else {})}
            if first_chunk_time is None:
                first_chunk_time = datetime.now().isoformat()

            mapping: Dict[str, FinishReason] = {
                "length": "length",  # Direct match
                "eos_token": "stop",  # EOS token means natural stop
                "stop_sequence": "stop",  # Stop sequence means natural stop
            }
            mapped_finish_reason = (
                mapping.get(chunk_metadata["finish_reason"], "stop") if chunk_metadata.get("finish_reason") else None
            )
            stream_chunk = StreamingChunk(
                content=token.text,
                meta=chunk_metadata,
                component_info=component_info,
                index=0,
                start=len(chunks) == 0,
                finish_reason=mapped_finish_reason,
            )
            chunks.append(stream_chunk)
            streaming_callback(stream_chunk)

        metadata = {
            "finish_reason": chunks[-1].meta.get("finish_reason", None),
            "model": self._client.model,
            "usage": {"completion_tokens": chunks[-1].meta.get("generated_tokens", 0)},
            "completion_start_time": first_chunk_time,
        }
        return {"replies": ["".join([chunk.content for chunk in chunks])], "meta": [metadata]}

    def _build_non_streaming_response(self, hf_output: "TextGenerationOutput"):
        meta = [
            {
                "model": self._client.model,
                "finish_reason": hf_output.details.finish_reason if hf_output.details else None,
                "usage": {"completion_tokens": len(hf_output.details.tokens) if hf_output.details else 0},
            }
        ]
        return {"replies": [hf_output.generated_text], "meta": meta}
