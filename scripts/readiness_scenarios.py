"""Readiness transcript scenario catalog."""

from __future__ import annotations

from scripts.readiness_models import (
    ReadinessExpectation,
    ReadinessScenario,
    ReadinessScenarioName,
    ReadinessTurn,
)
from shared.config.settings import settings


def readiness_scenarios() -> dict[str, ReadinessScenario]:
    app_name_hint = settings.app_name_short.lower()
    return {
        "core": ReadinessScenario(
            id="core",
            description="Identity, brand, and conversational grounding checks.",
            turns=(
                ReadinessTurn("Hi", ReadinessExpectation(expect_any=("what would you like", "help", app_name_hint))),
                ReadinessTurn(
                    "Hi Xara",
                    ReadinessExpectation(
                        expect_all=("Not Xara", "I'm"),
                        expect_path_shape="meta_direct",
                        expect_routing_owner="guardrail",
                    ),
                ),
                ReadinessTurn(
                    "What is the meaning of Nenya?",
                    ReadinessExpectation(expect_any=("Ring of Water", "liquidity", "flow")),
                ),
                ReadinessTurn("Okay, that's mental", ReadinessExpectation(expect_none=("transfer to", "Amount:"))),
            ),
        ),
        "transfer": ReadinessScenario(
            id="transfer",
            description="Transfer start, interruption, and resume behavior.",
            turns=(
                ReadinessTurn(
                    "Send 2k to Tolu Adebayo",
                    ReadinessExpectation(
                        expect_path_shape="deterministic_transfer_domain",
                        expect_task_types=("transfer",),
                    ),
                ),
                ReadinessTurn(
                    "Wait, what's my Access balance?",
                    ReadinessExpectation(expect_any=("balance", "access")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Yes please",
                    ReadinessExpectation(expect_any=("transfer", "tolu", "continue", "review")),
                    modes=("dry-run",),
                ),
            ),
        ),
        "data": ReadinessScenario(
            id="data",
            description="Catalog-grounded data query, buy, and plan-edit checks.",
            turns=(
                ReadinessTurn(
                    "buy 1gb data for me",
                    ReadinessExpectation(
                        expect_path_shape="deterministic_data_domain",
                        expect_task_types=("data",),
                    ),
                    modes=("deterministic",),
                ),
                ReadinessTurn(
                    "How much is 5GB MTN?",
                    ReadinessExpectation(expect_any=("MTN", "5", "₦", "valid")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Buy it",
                    ReadinessExpectation(
                        expect_any=("Confirm Data", "data", "MTN", "5"),
                        expect_async_job_count_delta=0,
                    ),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "What other plan within that range?",
                    ReadinessExpectation(expect_any=("option", "plan", "reply")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "option 2",
                    ReadinessExpectation(expect_any=("Confirm Data", "data", "₦")),
                    modes=("dry-run",),
                ),
            ),
        ),
        "airtime": ReadinessScenario(
            id="airtime",
            description="Airtime self and edit behavior.",
            turns=(
                ReadinessTurn(
                    "Buy me 1k airtime",
                    ReadinessExpectation(
                        expect_path_shape="deterministic_airtime_domain",
                        expect_task_types=("airtime",),
                    ),
                ),
                ReadinessTurn(
                    "make it 2k",
                    ReadinessExpectation(expect_any=("2,000", "airtime")),
                    modes=("dry-run",),
                ),
            ),
        ),
        "faq": ReadinessScenario(
            id="faq",
            description="FAQ answer, uncertainty, and support-boundary checks.",
            turns=(
                ReadinessTurn(
                    "What are transfer fees?",
                    ReadinessExpectation(expect_any=("fee", "confirm", "charge")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Can you export my transactions as CSV?",
                    ReadinessExpectation(expect_any=("csv", "not supported", "not available")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Why did my transfer fail?",
                    ReadinessExpectation(
                        expect_any=("reference", "transaction", "support", "failed"),
                        expect_none=("connect you with our support team",),
                    ),
                    modes=("dry-run",),
                ),
            ),
        ),
        "unsupported": ReadinessScenario(
            id="unsupported",
            description="Unsupported capability boundary and breakout checks.",
            turns=(
                ReadinessTurn(
                    "Can you borrow me money?",
                    ReadinessExpectation(expect_any=("can't help with loans", "can't help with lending")),
                ),
                ReadinessTurn(
                    "I will pay back",
                    ReadinessExpectation(
                        expect_any=("can't help with loans", "can't help with lending", "can’t help with loans"),
                        expect_none=("transfer to", "Amount:"),
                    ),
                ),
                ReadinessTurn(
                    "buy 1gb data for me",
                    ReadinessExpectation(expect_task_types=("data",), expect_path_shape="deterministic_data_domain"),
                ),
            ),
        ),
        "schedule": ReadinessScenario(
            id="schedule",
            description="Scheduled transaction read checks.",
            turns=(
                ReadinessTurn(
                    "Show my scheduled transactions",
                    ReadinessExpectation(expect_task_types=("schedule",)),
                ),
            ),
        ),
        "quick": ReadinessScenario(
            id="quick",
            description="Quick readiness smoke scenario.",
            turns=(
                ReadinessTurn("Hi", ReadinessExpectation(expect_any=("what would you like", "help", app_name_hint))),
                ReadinessTurn(
                    "Show my beneficiaries",
                    ReadinessExpectation(expect_any=("beneficiar", "tolu")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Is that all?",
                    ReadinessExpectation(expect_any=("3", "beneficiar", "saved")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Show my accounts",
                    ReadinessExpectation(expect_any=("account", "bank")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Which one is GTBank?",
                    ReadinessExpectation(expect_any=("gtbank", "0002", "account")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Why is Zenith pending?",
                    ReadinessExpectation(expect_any=("zenith", "pending", "authorization")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Send 2k to tolu",
                    ReadinessExpectation(expect_any=("tolu", "confirm", "which")),
                    modes=("dry-run",),
                ),
            ),
        ),
        "query": ReadinessScenario(
            id="query",
            description="Query readiness smoke scenario.",
            turns=(
                ReadinessTurn("Hi", ReadinessExpectation(expect_any=("what would you like", "help", app_name_hint))),
                ReadinessTurn(
                    "Show my recent transactions",
                    ReadinessExpectation(expect_any=("transaction", "showing", "sent", "received")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Show the 25k one",
                    ReadinessExpectation(expect_any=("25,000", "transaction details", "adebayo", "bank")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "What bank was that?",
                    ReadinessExpectation(expect_any=("bank", "zenith", "first", "gtbank", "access")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Now show the 3rd transaction",
                    ReadinessExpectation(expect_any=("transaction details", "amount", "bank")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "Back",
                    ReadinessExpectation(expect_any=("transaction", "showing", "more", "page")),
                    modes=("dry-run",),
                ),
                ReadinessTurn(
                    "More",
                    ReadinessExpectation(expect_any=("transaction", "showing", "more", "page")),
                    modes=("dry-run",),
                ),
            ),
        ),
    }


def resolve_scenarios(name: ReadinessScenarioName) -> tuple[ReadinessScenario, ...]:
    scenarios = readiness_scenarios()
    if name == "all":
        return tuple(
            scenarios[key]
            for key in ("core", "transfer", "data", "airtime", "faq", "unsupported", "schedule")
        )
    if name == "mvp":
        return tuple(scenarios[key] for key in ("quick", "transfer", "airtime"))
    if name == "query-deep":
        query = scenarios["query"]
        return (
            ReadinessScenario(
                id="query-deep",
                description="Deep query readiness scenario.",
                turns=(
                    *query.turns,
                    ReadinessTurn(
                        "Is this all?",
                        ReadinessExpectation(expect_any=("coverage", "local", "synced", "confirm", "complete")),
                        modes=("dry-run",),
                    ),
                    ReadinessTurn(
                        "Break down my spending by account this month",
                        ReadinessExpectation(expect_any=("breakdown", "account", "bank")),
                        modes=("dry-run",),
                    ),
                    ReadinessTurn(
                        "Which account did I spend from most?",
                        ReadinessExpectation(expect_any=("account", "spent", "₦", "bank")),
                        modes=("dry-run",),
                    ),
                    ReadinessTurn(
                        "How much did I spend on food this month?",
                        ReadinessExpectation(expect_any=("spent", "food", "₦", "transaction")),
                        modes=("dry-run",),
                    ),
                    ReadinessTurn(
                        "Show the transactions behind that",
                        ReadinessExpectation(expect_any=("transaction", "showing", "sent", "received")),
                        modes=("dry-run",),
                    ),
                    ReadinessTurn(
                        "Can I send 35k?",
                        ReadinessExpectation(expect_any=("cover", "₦35,000", "available", "shortfall", "breakdown")),
                        modes=("dry-run",),
                    ),
                    ReadinessTurn(
                        "Why are Zenith transactions missing?",
                        ReadinessExpectation(expect_any=("zenith", "coverage", "authorization", "sync")),
                        modes=("dry-run",),
                    ),
                ),
            ),
        )
    return (scenarios[name],)
