"""Architecture guards for the VPS-only Redis runtime path."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEXT_ROOTS = ("apps", "shared", "scripts", ".github", "tests")
TEXT_FILES = ("README.md", "Makefile", "docker-compose.yml", "deploy-stack.sh", "pyproject.toml")

FORBIDDEN_TEXT = (
    "BaseSQSHandler",
    "SQSPoller",
    "SNSPublisher",
    "sqs_queue_name",
    "resolve_contract_from_domain",
    "ASYNC_TRANSPORT=aws",
    "apps.gateway.lambda_handler",
    "apps.transaction.lambda_handler",
    "apps.receipt.lambda_handler",
    "apps.chat.src.lambda_handlers.scheduler_dispatcher_handler",
)

FORBIDDEN_FILES = (
    "apps/gateway/lambda_handler.py",
    "apps/transaction/lambda_handler.py",
    "apps/receipt/lambda_handler.py",
    "apps/chat/src/lambda_handlers/scheduler_dispatcher_handler.py",
    "shared/queue/lambda_base.py",
    "shared/queue/sqs_poller.py",
    "shared/queue/sns_publisher.py",
    "infrastructure/aws",
    "infrastructure/aws-backend-bootstrap",
)


def _text_files() -> list[Path]:
    files: list[Path] = []
    for root_name in TEXT_ROOTS:
        root = ROOT / root_name
        if root.exists():
            files.extend(path for path in root.rglob("*") if path.is_file() and path.suffix in {".py", ".sh", ".yml"})
    files.extend(ROOT / filename for filename in TEXT_FILES)
    return sorted(path for path in files if path.exists())


def test_removed_lambda_and_aws_queue_artifacts_do_not_exist() -> None:
    existing = [path for filename in FORBIDDEN_FILES if (path := ROOT / filename).exists()]

    assert existing == []


def test_vps_runtime_sources_do_not_reference_deleted_aws_queue_paths() -> None:
    this_file = Path(__file__).resolve()
    violations: list[str] = []
    for path in _text_files():
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_TEXT:
            if forbidden in text:
                violations.append(f"{path.relative_to(ROOT)} references {forbidden}")

    assert violations == []
