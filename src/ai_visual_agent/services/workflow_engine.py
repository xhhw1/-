from typing import Any

from langgraph.types import Command

from ai_visual_agent.domain import HumanReviewInput, ProjectRecord, WorkflowResult
from ai_visual_agent.graph.build import build_graph
from ai_visual_agent.services.asset_memory import project_file_memory_context
from ai_visual_agent.services.checkpoint import CheckpointerHandle, create_checkpointer_handle
from ai_visual_agent.services.interrupts import serialize_interrupts


class WorkflowEngine:
    """Thin application service over the compiled LangGraph workflow."""

    def __init__(self, checkpointer_handle: CheckpointerHandle | None = None) -> None:
        self.checkpointer_handle = checkpointer_handle or create_checkpointer_handle()
        self.graph = build_graph(checkpointer=self.checkpointer_handle.checkpointer)

    def start(self, project: ProjectRecord) -> WorkflowResult:
        state = {
            "project_id": project.id,
            "workflow_type": project.workflow_type,
            "project_brief": project.brief.model_dump(),
            "assets": [asset.model_dump() for asset in project.assets],
            "file_memory_context": project_file_memory_context(project),
            "revision_round": 0,
            "human_feedback": [],
            "status": "running",
        }
        raw = self.graph.invoke(state, config=self._config(project.id))
        return self._to_result(project.id, raw)

    def resume(self, project_id: str, review: HumanReviewInput) -> WorkflowResult:
        raw = self.graph.invoke(
            Command(resume=review.model_dump(mode="json")),
            config=self._config(project_id),
        )
        return self._to_result(project_id, raw)

    @staticmethod
    def _config(project_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": project_id}}

    @staticmethod
    def _to_result(project_id: str, raw: dict[str, Any]) -> WorkflowResult:
        interrupts = serialize_interrupts(raw.get("__interrupt__"))
        status = "waiting_review" if interrupts else raw.get("status", "running")
        return WorkflowResult(
            project_id=project_id,
            status=status,
            interrupts=interrupts,
            state={k: v for k, v in raw.items() if k != "__interrupt__"},
        )

    def close(self) -> None:
        self.checkpointer_handle.close()


workflow_engine = WorkflowEngine()
