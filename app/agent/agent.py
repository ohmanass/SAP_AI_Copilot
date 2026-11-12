import json

from app.llm.llm_client import LLMClient
from app.tools.purchase_orders import get_purchase_order
from app.tools.delivery_analysis import get_overdue_orders
from app.tools.sap_documentation import search_sap_documentation

# Décrit les fonctions disponibles pour l'agent, y compris leurs paramètres et descriptions.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_purchase_order",
            "description": (
                "Retrieve a purchase order from SAP MM "
                "using its purchase order number. "
                "Returns supplier, delivery date, status "
                "and purchase order items."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "po_number": {
                        "type": "string",
                        "description": "SAP purchase order number"
                    }
                },
                "required": ["po_number"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_overdue_orders",
            "description": (
                "Retrieve all purchase orders whose delivery "
                "date has passed and whose status is not RECEIVED."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_sap_documentation",
            "description": """
            Search the SAP technical documentation for information
            about SAP tables, ABAP, CDS Views, OData, Fiori,
            SAP MM, SAP BTP and other SAP technical concepts.

            Use this tool for technical questions that require
            documentation.

            It can be combined with transactional database tools
            when the user asks for both data analysis and technical explanations.
            """,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The technical question to search for."
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Number of relevant document chunks to retrieve."
                    }
                },
                "required": ["query"],
                "additionalProperties": False
            }
        }
    }
]


# Contient les véritables fonctions qui seront appelées par l'agent pour exécuter les outils définis ci-dessus.
TOOL_REGISTRY = {
    "get_purchase_order": get_purchase_order,
    "get_overdue_orders": get_overdue_orders,
    "search_sap_documentation": search_sap_documentation
}

MAX_ROUNDS_MESSAGE = "Maximum number of tool-calling rounds reached."


class SAPAgent:

    def __init__(self):

        self.llm = LLMClient()

        self.messages = [
            {
                "role": "system",
                "content": 
                    """
                    You are SAP AI Copilot, an intelligent SAP technical consultant.

                    You specialize in:
                    - SAP S/4HANA
                    - SAP MM
                    - ABAP
                    - CDS Views
                    - OData
                    - SAP Fiori
                    - SAP BTP

                    You have access to three tools:

                    1. get_purchase_order:
                    Retrieves transactional purchase order information.

                    2. get_overdue_orders:
                    Retrieves overdue purchase orders.

                    3. search_sap_documentation:
                    Searches the SAP technical documentation using RAG.

                    TOOL USAGE RULES:

                    - Use database tools for transactional information.
                    - Use search_sap_documentation for technical questions.
                    - Use multiple tools when the question requires both
                    transactional data and technical documentation.
                    - Never invent database information.
                    - Never invent documentation references.
                    - If documentation is insufficient, explicitly say so.
                    - Distinguish database facts from documentation information.
                    - Cite documentation using its filename and chunk number.
                    - Answer in the same language as the user.
                    - Explain technical concepts clearly.
                    - For documentation-based answers, use only information
                    explicitly supported by retrieved documents.
                    - Never introduce SAP transaction codes, tables,
                    configuration steps or procedures absent from the retrieved context.
                    - Never present general model knowledge as retrieved documentation.
                    - If the retrieved documentation is incomplete,
                    explicitly identify what is missing.
                    - Clearly distinguish transactional database facts
                    from documented technical knowledge.
                    - Never speculate about the causes of delivery delays.

                    """

            }
        ]

    def execute_tool(self, tool_name, arguments):

        if tool_name not in TOOL_REGISTRY:
            raise ValueError(f"Unknown tool: {tool_name}")

        function = TOOL_REGISTRY[tool_name]

        return function(**arguments)

    def restore_history(self, history):
        """
        Rebuild the conversation context from persisted messages
        (user / assistant text only), e.g. after a backend restart.
        """

        for entry in history:
            if entry.get("role") in ("user", "assistant") and entry.get("content"):
                self.messages.append({
                    "role": entry["role"],
                    "content": entry["content"]
                })

    def ask(self, user_input):

        return self.ask_with_details(user_input)["response"]

    def ask_with_details(self, user_input):
        """
        Same behaviour as ask(), but also returns the tools that were
        called and the documentation chunks actually retrieved by RAG.

        Returns:
            {
                "response": str,
                "tools_used": [str, ...],
                "sources": [{"source", "chunk", "distance", "content"}, ...]
            }
        """

        history_length = len(self.messages)
        tools_used = []
        sources = []

        try:
            answer = self._run(user_input, tools_used, sources)
        except Exception:
            # Keep the conversation consistent if the LLM call fails
            del self.messages[history_length:]
            raise

        return {
            "response": answer,
            "tools_used": tools_used,
            "sources": sources
        }

    @staticmethod
    def _collect_sources(tool_name, result, sources):

        if tool_name != "search_sap_documentation":
            return

        if not isinstance(result, dict) or not result.get("found"):
            return

        known = {(s["source"], s["chunk"]) for s in sources}

        for document in result.get("documents", []):
            key = (document.get("source"), document.get("chunk"))

            if key in known:
                continue

            known.add(key)
            sources.append({
                "source": document.get("source"),
                "chunk": document.get("chunk"),
                "distance": document.get("distance"),
                "content": document.get("content")
            })

    def _run(self, user_input, tools_used, sources):

        self.messages.append({
            "role": "user",
            "content": user_input
        })

        # Limit the number of tool-calling rounds
        for _ in range(5):

            response = self.llm.generate_completion(
                messages=self.messages,
                tools=TOOLS
            )

            message = response.choices[0].message

            tool_calls = message.tool_calls

            if not tool_calls:

                answer = message.content or ""

                self.messages.append({
                    "role": "assistant",
                    "content": answer
                })

                return answer

            # Preserve the assistant's tool call request
            assistant_message = {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments
                        }
                    }
                    for call in tool_calls
                ]
            }

            self.messages.append(assistant_message)

            # Execute each requested tool
            for call in tool_calls:
                self._execute_call(
                    call.id,
                    call.function.name,
                    call.function.arguments,
                    tools_used,
                    sources
                )

        return MAX_ROUNDS_MESSAGE

    def _execute_call(self, call_id, tool_name, raw_arguments, tools_used, sources):
        """Run one tool call, record it and append its result to the conversation."""

        if tool_name not in tools_used:
            tools_used.append(tool_name)

        try:
            arguments = json.loads(raw_arguments or "{}")

            result = self.execute_tool(
                tool_name,
                arguments
            )

            self._collect_sources(tool_name, result, sources)

            tool_result = {
                "success": True,
                "data": result
            }

        except Exception as error:

            tool_result = {
                "success": False,
                "error": str(error)
            }

        self.messages.append({
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(
                tool_result,
                ensure_ascii=False
            )
        })

        return tool_result["success"]

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def ask_stream(self, user_input):
        """
        Streaming variant of ask_with_details(). Yields events:

            {"type": "tool_start", "name": str}
            {"type": "tool_end", "name": str, "success": bool}
            {"type": "token", "content": str}
            {"type": "done", "response": str, "tools_used": [...], "sources": [...]}

        If the generator fails or is closed early (client disconnected),
        the conversation is rolled back to its previous state.
        """

        history_length = len(self.messages)
        tools_used = []
        sources = []
        completed = False

        try:
            self.messages.append({
                "role": "user",
                "content": user_input
            })

            for _ in range(5):

                content_parts = []
                calls = {}

                stream = self.llm.generate_completion(
                    messages=self.messages,
                    tools=TOOLS,
                    stream=True
                )

                for chunk in stream:

                    # Content-filter chunks carry no choices
                    if not chunk.choices:
                        continue

                    delta = chunk.choices[0].delta

                    if delta.content:
                        content_parts.append(delta.content)
                        yield {"type": "token", "content": delta.content}

                    # Tool calls arrive as fragments, grouped by index
                    for fragment in delta.tool_calls or []:
                        call = calls.setdefault(
                            fragment.index,
                            {"id": None, "name": "", "arguments": ""}
                        )
                        if fragment.id:
                            call["id"] = fragment.id
                        if fragment.function and fragment.function.name:
                            call["name"] += fragment.function.name
                        if fragment.function and fragment.function.arguments:
                            call["arguments"] += fragment.function.arguments

                content = "".join(content_parts)

                if not calls:
                    self.messages.append({
                        "role": "assistant",
                        "content": content
                    })
                    completed = True
                    yield {
                        "type": "done",
                        "response": content,
                        "tools_used": tools_used,
                        "sources": sources
                    }
                    return

                ordered_calls = [calls[index] for index in sorted(calls)]

                self.messages.append({
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments"]
                            }
                        }
                        for call in ordered_calls
                    ]
                })

                for call in ordered_calls:
                    yield {"type": "tool_start", "name": call["name"]}

                    success = self._execute_call(
                        call["id"],
                        call["name"],
                        call["arguments"],
                        tools_used,
                        sources
                    )

                    yield {"type": "tool_end", "name": call["name"], "success": success}

            self.messages.append({
                "role": "assistant",
                "content": MAX_ROUNDS_MESSAGE
            })
            completed = True
            yield {
                "type": "done",
                "response": MAX_ROUNDS_MESSAGE,
                "tools_used": tools_used,
                "sources": sources
            }

        finally:
            # Error or early close (GeneratorExit): keep the history consistent
            if not completed:
                del self.messages[history_length:]