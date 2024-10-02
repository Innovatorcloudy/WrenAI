import json
import logging
import sys
from pathlib import Path
from typing import Any

import orjson
from hamilton import base
from hamilton.experimental.h_async import AsyncDriver
from haystack.components.builders.prompt_builder import PromptBuilder
from langfuse.decorators import observe

from src.core.pipeline import BasicPipeline, async_validate
from src.core.provider import LLMProvider

logger = logging.getLogger("wren-ai-service")


## Start of Pipeline
def picked_models(mdl: dict, selected_models: list[str]) -> list[dict]:
    def extract(model: dict) -> dict:
        return {
            "name": model["name"],
            "columns": model["columns"],
            "properties": model["properties"],
        }

    return [
        extract(model) for model in mdl["models"] if model["name"] in selected_models
    ]


def prompt(
    picked_models: list[dict],
    user_prompt: str,
    prompt_builder: PromptBuilder,
) -> dict:
    return prompt_builder.run(picked_models=picked_models, user_prompt=user_prompt)


async def generate(prompt: dict, generator: Any) -> dict:
    return await generator.run(prompt=prompt.get("prompt"))


def post_process(generate: dict) -> dict:
    def normalize(text: str) -> str:
        text = text.replace("\n", " ")
        text = " ".join(text.split())
        # Convert the normalized text to a dictionary
        try:
            text_dict = orjson.loads(text.strip())
            return text_dict
        except orjson.JSONDecodeError as e:
            logger.error(f"Error decoding JSON: {e}")
            return {}  # Return an empty dictionary if JSON decoding fails

    reply = generate.get("replies")[0]  # Expecting only one reply
    normalized = normalize(reply)

    return {model["name"]: model for model in normalized["models"]}


## End of Pipeline

system_prompt = """ 
I have a data model represented in JSON format, with the following structure:

```
[
    {'name': 'model', 'columns': [
            {'name': 'column_1', 'type': 'type', 'notNull': True, 'properties': {}
            },
            {'name': 'column_2', 'type': 'type', 'notNull': True, 'properties': {}
            },
            {'name': 'column_3', 'type': 'type', 'notNull': False, 'properties': {}
            }
        ], 'properties': {}
    }
]
```

Your task is to update this JSON structure by adding a `description` field inside both the `properties` attribute of each `column` and the `model` itself. 
Each `description` should be derived from a user-provided input that explains the purpose or context of the `model` and its respective columns. 
Follow these steps:
1. **For the `model`**: Prompt the user to provide a brief description of the model's overall purpose or its context. Insert this description in the `properties` field of the `model`.
2. **For each `column`**: Ask the user to describe each column's role or significance. Each column's description should be added under its respective `properties` field in the format: `'description': 'user-provided text'`.
3. Ensure that the output is a well-formatted JSON structure, preserving the input's original format and adding the appropriate `description` fields.

### Output Format:

```
[
    {
        "name": "model",
        "columns": [
            {
                "name": "column_1",
                "properties": {
                    "description": "<description for column_1>"
                }
            },
            {
                "name": "column_2",
                "properties": {
                    "description": "<description for column_1>"
                }
            },
            {
                "name": "column_3",
                "properties": {
                    "description": "<description for column_1>"
                }
            }
        ],
        "properties": {
            "description": "<description for model>"
        }
    }
]
```

Make sure that the descriptions are concise, informative, and contextually appropriate based on the input provided by the user.
"""

user_prompt_template = """

### Input
User's prompt: {{ user_prompt }}
Picked models: {{ picked_models }}

Please provide a brief description for the model and each column based on the user's prompt.
"""


class SemanticsDescription(BasicPipeline):
    def __init__(
        self,
        llm_provider: LLMProvider,
    ):
        self._components = {
            "prompt_builder": PromptBuilder(template=user_prompt_template),
            "generator": llm_provider.get_generator(system_prompt=system_prompt),
        }

        super().__init__(
            AsyncDriver({}, sys.modules[__name__], result_builder=base.DictResult())
        )

    def visualize(
        self,
        user_prompt: str,
        selected_models: list[str],
        mdl: dict,
    ) -> None:
        destination = "outputs/pipelines/generation"
        if not Path(destination).exists():
            Path(destination).mkdir(parents=True, exist_ok=True)

        self._pipe.visualize_execution(
            [""],
            output_file_path=f"{destination}/semantics_description.dot",
            inputs={
                "user_prompt": user_prompt,
                "selected_models": selected_models,
                "mdl": mdl,
                **self._components,
            },
            show_legend=True,
            orient="LR",
        )

    @observe(name="Semantics Description Generation")
    async def run(
        self,
        user_prompt: str,
        selected_models: list[str],
        mdl: dict,
    ) -> dict:
        logger.info("Semantics Description Generation pipeline is running...")
        return await self._pipe.execute(
            ["post_process"],
            inputs={
                "user_prompt": user_prompt,
                "selected_models": selected_models,
                "mdl": mdl,
                **self._components,
            },
        )


if __name__ == "__main__":
    from src.core.engine import EngineConfig
    from src.core.pipeline import async_validate
    from src.providers import init_providers
    from src.utils import init_langfuse, load_env_vars

    load_env_vars()
    init_langfuse()

    llm_provider, _, _, _ = init_providers(EngineConfig())
    pipeline = SemanticsDescription(llm_provider=llm_provider)

    with open("src/pipelines/prototype/example.json", "r") as file:
        mdl = json.load(file)

    input = {
        "user_prompt": "The Orders and Customers dataset is utilized to analyze customer behavior and preferences over time, enabling the improvement of marketing strategies. By examining purchasing patterns and trends, businesses can tailor their marketing efforts to better meet customer needs and enhance engagement.",
        "selected_models": ["orders", "customers"],
        "mdl": mdl,
    }

    # pipeline.visualize(**input)
    async_validate(lambda: pipeline.run(**input))

    # expected = {
    #     "model_name": ["column1", "column2"],
    # }

    # langfuse_context.flush()
