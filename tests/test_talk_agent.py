from langchain_core.messages import HumanMessage

from backend.agent_graph import TalkAgentGraph
from backend.config import Settings


class RuntimeStub:
    async def generate(self, messages, token_queue=None):
        if token_queue is not None:
            await token_queue.put("Hello")
        return "Hello"


async def test_talk_router_requests_visual_for_math():
    agent = TalkAgentGraph(RuntimeStub(), Settings(phoenix_enabled=False))
    result = await agent._route_visual(
        {
            "messages": [HumanMessage(content="Visualize this equation")],
            "voice_input": "Visualize this equation",
            "requires_animation": False,
            "user_preferences": {},
            "response": "",
            "token_queue": None,
        }
    )
    assert result["requires_animation"] is True


async def test_talk_graph_preserves_multi_turn_state():
    agent = TalkAgentGraph(RuntimeStub(), Settings(phoenix_enabled=False))
    result = await agent.invoke([], "Hello friend", {})
    assert result["response"] == "Hello"
    assert result["messages"][-1].content == "Hello"
