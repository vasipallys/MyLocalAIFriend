import asyncio
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from backend.config import Settings
from backend.model import GemmaRuntime


TALK_SYSTEM_PROMPT = """You are a warm, thoughtful voice companion named Gemma.
Talk naturally like a trusted friend. Be concise enough to speak aloud, usually under 180 words.
Never use Markdown tables. Ask at most one gentle follow-up question. If the user asks for a
mathematical, scientific, algorithmic, or conceptual explanation, make the explanation structured
and concrete so it can also be visualized."""


class TalkState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    voice_input: str
    requires_animation: bool
    user_preferences: dict[str, str]
    response: str
    token_queue: asyncio.Queue[str] | None


class TalkAgentGraph:
    VISUAL_TERMS = {
        "visualize", "animation", "animate", "diagram", "graph", "geometry", "equation",
        "algorithm", "calculus", "physics", "matrix", "probability", "explain visually",
    }

    def __init__(self, runtime: GemmaRuntime, settings: Settings):
        self.runtime = runtime
        self.settings = settings
        graph = StateGraph(TalkState)
        graph.add_node("route_visual", self._route_visual)
        graph.add_node("companion", self._companion)
        graph.add_edge(START, "route_visual")
        graph.add_edge("route_visual", "companion")
        graph.add_edge("companion", END)
        self.graph = graph.compile()

    async def _route_visual(self, state: TalkState) -> dict:
        lowered = state["voice_input"].lower()
        return {"requires_animation": any(term in lowered for term in self.VISUAL_TERMS)}

    async def _companion(self, state: TalkState) -> dict:
        messages = [{"role": "system", "content": TALK_SYSTEM_PROMPT}]
        turns: list[dict[str, str]] = []
        for message in state["messages"][-self.settings.model_context_messages:]:
            role = "assistant" if isinstance(message, AIMessage) else "user"
            content = str(message.content)
            if not turns and role == "assistant":
                continue
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"] += "\n\n" + content
            else:
                turns.append({"role": role, "content": content})
        messages.extend(turns)
        response = await self.runtime.generate(messages, state.get("token_queue"))
        return {"response": response, "messages": [AIMessage(content=response)]}

    async def invoke(
        self,
        history: list[BaseMessage],
        transcript: str,
        preferences: dict[str, str],
        token_queue: asyncio.Queue[str] | None = None,
    ) -> TalkState:
        return await self.graph.ainvoke(
            {
                "messages": history + [HumanMessage(content=transcript)],
                "voice_input": transcript,
                "requires_animation": False,
                "user_preferences": preferences,
                "response": "",
                "token_queue": token_queue,
            }
        )

