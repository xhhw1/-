from pathlib import Path

from ai_visual_agent.graph.nodes import quality_check_node


PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"


def _asset(tmp_path: Path, name: str) -> str:
    path = tmp_path / f"{name}.png"
    path.write_bytes(PNG_BYTES)
    return str(path)


def _output(tmp_path: Path, name: str, prompt: str) -> dict:
    return {
        "name": name,
        "asset_id": f"{name}-asset",
        "uri": _asset(tmp_path, name),
        "prompt": prompt,
        "layout_spec": {"surface": name},
    }


def test_packaging_qc_passes_when_required_faces_and_copy_exist(tmp_path) -> None:
    state = {
        "project_id": "qc-pass",
        "workflow_type": "packaging",
        "packaging_strategy": {"required_copy": ["Hero play proof"]},
        "generated_outputs": {
            "items": [
                _output(tmp_path, "front", "Hero play proof front layout"),
                _output(tmp_path, "left", "left layout"),
                _output(tmp_path, "right", "right layout"),
                _output(tmp_path, "back", "back layout"),
            ]
        },
    }

    result = quality_check_node(state)

    report = result["qc_report"]
    assert result["status"] == "qc_passed"
    assert report["passed"] is True
    assert report["issues"] == []
    assert report["score"] == 0.95


def test_packaging_qc_blocks_missing_required_face(tmp_path) -> None:
    state = {
        "project_id": "qc-missing-face",
        "workflow_type": "packaging",
        "packaging_strategy": {"required_copy": ["Hero play proof"]},
        "generated_outputs": {
            "items": [
                _output(tmp_path, "front", "Hero play proof front layout"),
                _output(tmp_path, "left", "left layout"),
                _output(tmp_path, "right", "right layout"),
            ]
        },
    }

    result = quality_check_node(state)

    report = result["qc_report"]
    assert result["status"] == "qc_failed"
    assert report["passed"] is False
    assert report["issues"][0]["severity"] == "blocking"
    assert "back" in report["issues"][0]["message"]


def test_packaging_qc_warns_when_required_copy_missing_from_front(tmp_path) -> None:
    state = {
        "project_id": "qc-copy-warning",
        "workflow_type": "packaging",
        "packaging_strategy": {"required_copy": ["Hero play proof"]},
        "generated_outputs": {
            "items": [
                _output(tmp_path, "front", "front layout without required copy"),
                _output(tmp_path, "left", "left layout"),
                _output(tmp_path, "right", "right layout"),
                _output(tmp_path, "back", "back layout"),
            ]
        },
    }

    result = quality_check_node(state)

    report = result["qc_report"]
    assert result["status"] == "qc_passed"
    assert report["passed"] is True
    assert report["issues"][0]["severity"] == "medium"
    assert report["issues"][0]["category"] == "copy"


def test_qc_blocks_missing_asset_file(tmp_path) -> None:
    state = {
        "project_id": "qc-missing-file",
        "workflow_type": "packaging",
        "packaging_strategy": {"required_copy": []},
        "generated_outputs": {
            "items": [
                _output(tmp_path, "front", "front layout"),
                _output(tmp_path, "left", "left layout"),
                _output(tmp_path, "right", "right layout"),
                {
                    "name": "back",
                    "asset_id": "back-asset",
                    "uri": str(tmp_path / "missing.png"),
                    "prompt": "back layout",
                    "layout_spec": {"surface": "back"},
                },
            ]
        },
    }

    result = quality_check_node(state)

    report = result["qc_report"]
    assert result["status"] == "qc_failed"
    assert report["passed"] is False
    assert any("does not exist" in issue["message"] for issue in report["issues"])
