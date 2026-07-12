from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import AgentNodes
from app.agent.routing import reliability_router, route_after_tool_execution
from app.agent.state import AgentState


def build_telecom_agent(*, checkpointer=None):
    """
    Biên dịch và đóng gói sơ đồ thực thể StateGraph cốt lõi cho hệ thống.
    """
    workflow = StateGraph(AgentState)

    # 1. Nodes xử lý logic vào bản đồ
    workflow.add_node("call_llm_gateway", AgentNodes.call_llm_gateway)
    workflow.add_node("execute_tools", AgentNodes.execute_tools)
    workflow.add_node("suspend_for_human", AgentNodes.suspend_for_human)
    workflow.add_node("fail", AgentNodes.fail_unsafe_or_exhausted)

    # 2. Thiết lập các đường đi mặc định cố định
    workflow.add_edge(START, "call_llm_gateway")
    workflow.add_conditional_edges(
        "execute_tools",
        route_after_tool_execution,
        {"call_llm_gateway": "call_llm_gateway", "end": END},
    )
    workflow.add_conditional_edges(
        "suspend_for_human",
        route_after_tool_execution,
        {"call_llm_gateway": "call_llm_gateway", "end": END},
    )
    workflow.add_edge("fail", END)

    # 3. Thiết lập chốt chặn rẽ nhánh thông minh tự động (Conditional Edges)
    workflow.add_conditional_edges(
        "call_llm_gateway",
        reliability_router,
        {
            "call_llm_gateway": "call_llm_gateway",
            "execute_tools": "execute_tools",
            "suspend_for_human": "suspend_for_human",
            "fail": "fail",
            "end": END,
        },
    )

    # 4. Đúc kết thành một cỗ máy Engine hoàn chỉnh.
    # Checkpointer được inject từ FastAPI lifespan/service, không khởi tạo ở import time.
    return workflow.compile(checkpointer=checkpointer)
