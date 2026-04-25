"""监督局部回写触发条件单测。"""

from app.orchestration.runner import should_run_supervisor_local_revision


def test_should_run_supervisor_local_revision_beat() -> None:
    assert should_run_supervisor_local_revision({"beat_alignment": "weak", "issues": []})
    assert should_run_supervisor_local_revision({"beat_alignment": "off"})
    assert not should_run_supervisor_local_revision({"beat_alignment": "ok", "issues": []})


def test_should_run_supervisor_local_revision_issue_severity() -> None:
    r = {
        "beat_alignment": "ok",
        "issues": [
            {"severity": "low", "target_agent": "Writer"},
            {"severity": "med", "target_agent": "Writer"},
        ],
    }
    assert should_run_supervisor_local_revision(r)


def test_should_run_supervisor_local_revision_ignores_unrelated_target() -> None:
    r = {
        "beat_alignment": "ok",
        "issues": [{"severity": "high", "target_agent": "None"}],
    }
    assert not should_run_supervisor_local_revision(r)
