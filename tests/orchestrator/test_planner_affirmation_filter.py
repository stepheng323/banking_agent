from types import SimpleNamespace

from apps.chat.src.agent.orchestrator.nodes.planner import _filter_spurious_affirmation_tasks


def test_affirmation_filter_removes_support_when_resume_present() -> None:
    planner_output = SimpleNamespace(
        tasks=[
            SimpleNamespace(executor="orchestrator", action="resume_session"),
            SimpleNamespace(executor="support", action="report_issue"),
        ],
        primary_intent="mixed",
        is_complex=True,
        is_confirmation=True,
    )

    filtered = _filter_spurious_affirmation_tasks(
        planner_output,
        active_intent=None,
        pending_interrupt_kind=None,
    )

    assert len(filtered.tasks) == 1
    assert filtered.tasks[0].executor == "orchestrator"
    assert filtered.tasks[0].action == "resume_session"
    assert filtered.primary_intent == "orchestrator"
    assert filtered.is_complex is False


def test_affirmation_filter_keeps_support_without_resume_context() -> None:
    planner_output = SimpleNamespace(
        tasks=[SimpleNamespace(executor="support", action="report_issue")],
        primary_intent="support",
        is_complex=False,
        is_confirmation=False,
    )

    filtered = _filter_spurious_affirmation_tasks(
        planner_output,
        active_intent=None,
        pending_interrupt_kind=None,
    )

    assert len(filtered.tasks) == 1
    assert filtered.tasks[0].executor == "support"


def test_affirmation_filter_removes_support_during_transfer_input() -> None:
    planner_output = SimpleNamespace(
        tasks=[
            SimpleNamespace(executor="transfer", action="send_money"),
            SimpleNamespace(executor="support", action="report_issue"),
        ],
        primary_intent="mixed",
        is_complex=True,
        is_confirmation=True,
    )

    filtered = _filter_spurious_affirmation_tasks(
        planner_output,
        active_intent="transfer",
        pending_interrupt_kind="input",
    )

    assert len(filtered.tasks) == 1
    assert filtered.tasks[0].executor == "transfer"
