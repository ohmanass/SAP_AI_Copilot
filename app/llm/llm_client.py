import os

from dotenv import load_dotenv
from gen_ai_hub.proxy.native.openai import chat

load_dotenv()


class LLMClient:

    def __init__(self):
        self.model = os.getenv(
            "GENAI_MODEL_NAME",
            "gpt-4o-mini"
        )

    def generate_completion(
        self,
        messages,
        tools=None,
        temperature=0,
        stream=False
    ):

        parameters = {
            "model_name": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1000
        }

        if tools is not None:
            parameters["tools"] = tools
            parameters["tool_choice"] = "auto"

        if stream:
            # Returns an iterator of chunks instead of a single completion
            parameters["stream"] = True

        return chat.completions.create(**parameters)

    def generate_response(self, messages):

        response = self.generate_completion(messages)

        return response.choices[0].message.content