from ai_visual_agent.domain import ProjectBrief, ProjectCreateRequest


def test_project_create_request_defaults() -> None:
    request = ProjectCreateRequest(
        workflow_type="packaging",
        brief=ProjectBrief(
            category="玩具",
            target_user="亲子家庭",
            user_expectations=["安全", "好玩"],
            value_proposition="更强互动体验",
            core_product_definition="互动玩具套装",
        ),
    )

    assert request.workflow_type == "packaging"
    assert request.assets == []
    assert request.brief.category == "玩具"
