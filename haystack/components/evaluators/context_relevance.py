# SPDX-FileCopyrightText: 2022-present deepset GmbH <info@deepset.ai>
#
# SPDX-License-Identifier: Apache-2.0

from statistics import mean
from typing import Any, Dict, List, Optional

from haystack import component, default_from_dict, default_to_dict
from haystack.components.evaluators.llm_evaluator import LLMEvaluator
from haystack.components.generators.chat.types import ChatGenerator
from haystack.core.serialization import component_to_dict
from haystack.utils import deserialize_chatgenerator_inplace

# Private global variable for default examples to include in the prompt if the user does not provide any examples
_DEFAULT_EXAMPLES = [
    {
        "inputs": {
            "questions": "What is the capital of Germany?",
            "contexts": ["Berlin is the capital of Germany. Berlin and was founded in 1244."],
        },
        "outputs": {"relevant_statements": ["Berlin is the capital of Germany."]},
    },
    {
        "inputs": {
            "questions": "What is the capital of France?",
            "contexts": [
                "Berlin is the capital of Germany and was founded in 1244.",
                "Europe is a continent with 44 countries.",
                "Madrid is the capital of Spain.",
            ],
        },
        "outputs": {"relevant_statements": []},
    },
    {
        "inputs": {"questions": "What is the capital of Italy?", "contexts": ["Rome is the capital of Italy."]},
        "outputs": {"relevant_statements": ["Rome is the capital of Italy."]},
    },
]


@component
class ContextRelevanceEvaluator(LLMEvaluator):
    """
    Evaluator that checks if a provided context is relevant to the question.

    An LLM breaks up a context into multiple statements and checks whether each statement
    is relevant for answering a question.
    The score for each context is either binary score of 1 or 0, where 1 indicates that the context is relevant
    to the question and 0 indicates that the context is not relevant.
    The evaluator also provides the relevant statements from the context and an average score over all the provided
    input questions contexts pairs.

    Usage example:
    ```python
    from haystack.components.evaluators import ContextRelevanceEvaluator

    questions = ["Who created the Python language?", "Why does Java needs a JVM?", "Is C++ better than Python?"]
    contexts = [
        [(
            "Python, created by Guido van Rossum in the late 1980s, is a high-level general-purpose programming "
            "language. Its design philosophy emphasizes code readability, and its language constructs aim to help "
            "programmers write clear, logical code for both small and large-scale software projects."
        )],
        [(
            "Java is a high-level, class-based, object-oriented programming language that is designed to have as few "
            "implementation dependencies as possible. The JVM has two primary functions: to allow Java programs to run"
            "on any device or operating system (known as the 'write once, run anywhere' principle), and to manage and"
            "optimize program memory."
        )],
        [(
            "C++ is a general-purpose programming language created by Bjarne Stroustrup as an extension of the C "
            "programming language."
        )],
    ]

    evaluator = ContextRelevanceEvaluator()
    result = evaluator.run(questions=questions, contexts=contexts)
    print(result["score"])
    # 0.67
    print(result["individual_scores"])
    # [1,1,0]
    print(result["results"])
    # [{
    #   'relevant_statements': ['Python, created by Guido van Rossum in the late 1980s.'],
    #    'score': 1.0
    #  },
    #  {
    #   'relevant_statements': ['The JVM has two primary functions: to allow Java programs to run on any device or
    #                           operating system (known as the "write once, run anywhere" principle), and to manage and
    #                           optimize program memory'],
    #   'score': 1.0
    #  },
    #  {
    #   'relevant_statements': [],
    #   'score': 0.0
    #  }]
    ```
    """

    def __init__(  # pylint: disable=too-many-positional-arguments
        self,
        examples: Optional[List[Dict[str, Any]]] = None,
        progress_bar: bool = True,
        raise_on_failure: bool = True,
        chat_generator: Optional[ChatGenerator] = None,
    ):
        """
        Creates an instance of ContextRelevanceEvaluator.

        If no LLM is specified using the `chat_generator` parameter, the component will use OpenAI in JSON mode.

        :param examples:
            Optional few-shot examples conforming to the expected input and output format of ContextRelevanceEvaluator.
            Default examples will be used if none are provided.
            Each example must be a dictionary with keys "inputs" and "outputs".
            "inputs" must be a dictionary with keys "questions" and "contexts".
            "outputs" must be a dictionary with "relevant_statements".
            Expected format:
            ```python
            [{
                "inputs": {
                    "questions": "What is the capital of Italy?", "contexts": ["Rome is the capital of Italy."],
                },
                "outputs": {
                    "relevant_statements": ["Rome is the capital of Italy."],
                },
            }]
            ```
        :param progress_bar:
            Whether to show a progress bar during the evaluation.
        :param raise_on_failure:
            Whether to raise an exception if the API call fails.
        :param chat_generator:
            a ChatGenerator instance which represents the LLM.
            In order for the component to work, the LLM should be configured to return a JSON object. For example,
            when using the OpenAIChatGenerator, you should pass `{"response_format": {"type": "json_object"}}` in the
            `generation_kwargs`.
        """

        self.instructions = (
            "Please extract only sentences from the provided context which are absolutely relevant and "
            "required to answer the following question. If no relevant sentences are found, or if you "
            "believe the question cannot be answered from the given context, return an empty list, example: []"
        )
        self.inputs = [("questions", List[str]), ("contexts", List[List[str]])]
        self.outputs = ["relevant_statements"]
        self.examples = examples or _DEFAULT_EXAMPLES

        super(ContextRelevanceEvaluator, self).__init__(
            instructions=self.instructions,
            inputs=self.inputs,
            outputs=self.outputs,
            examples=self.examples,
            chat_generator=chat_generator,
            raise_on_failure=raise_on_failure,
            progress_bar=progress_bar,
        )

    @component.output_types(score=float, results=List[Dict[str, Any]])
    def run(self, **inputs) -> Dict[str, Any]:
        """
        Run the LLM evaluator.

        :param questions:
            A list of questions.
        :param contexts:
            A list of lists of contexts. Each list of contexts corresponds to one question.
        :returns:
            A dictionary with the following outputs:
                - `score`: Mean context relevance score over all the provided input questions.
                - `results`: A list of dictionaries with `relevant_statements` and `score` for each input context.
        """
        result = super(ContextRelevanceEvaluator, self).run(**inputs)

        for idx, res in enumerate(result["results"]):
            if res is None:
                result["results"][idx] = {"relevant_statements": [], "score": float("nan")}
                continue
            if len(res["relevant_statements"]) > 0:
                res["score"] = 1
            else:
                res["score"] = 0

        # calculate average context relevance score over all queries
        result["score"] = mean([res["score"] for res in result["results"]])
        result["individual_scores"] = [res["score"] for res in result["results"]]  # useful for the EvaluationRunResult

        return result

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize this component to a dictionary.

        :returns:
            A dictionary with serialized data.
        """
        return default_to_dict(
            self,
            chat_generator=component_to_dict(obj=self._chat_generator, name="chat_generator"),
            examples=self.examples,
            progress_bar=self.progress_bar,
            raise_on_failure=self.raise_on_failure,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContextRelevanceEvaluator":
        """
        Deserialize this component from a dictionary.

        :param data:
            The dictionary representation of this component.
        :returns:
            The deserialized component instance.
        """
        if data["init_parameters"].get("chat_generator"):
            deserialize_chatgenerator_inplace(data["init_parameters"], key="chat_generator")
        return default_from_dict(cls, data)
