"""Architecture guards for domains moved out of the chat app."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = ("apps", "banking", "scripts", "tests")

CHAT_WORKER_MODULE = ".".join(("apps", "chat", "src", "agent", "workers"))
CHAT_AGENT_MODULE = ".".join(("apps", "chat", "src", "agent"))

MOVED_WORKER_MODULES = (
    CHAT_WORKER_MODULE,
    f"{CHAT_WORKER_MODULE}.faq",
    f"{CHAT_WORKER_MODULE}.account",
    f"{CHAT_WORKER_MODULE}.beneficiary",
    f"{CHAT_WORKER_MODULE}.onboarding",
    f"{CHAT_WORKER_MODULE}.__shared__",
    f"{CHAT_WORKER_MODULE}.airtime",
    f"{CHAT_WORKER_MODULE}.data",
    f"{CHAT_WORKER_MODULE}.transfer",
    f"{CHAT_WORKER_MODULE}.support",
    f"{CHAT_WORKER_MODULE}.query",
    f"{CHAT_AGENT_MODULE}.protocols",
    f"{CHAT_AGENT_MODULE}.shared",
    f"{CHAT_AGENT_MODULE}.orchestrator.confirmation.confirmation_classifier",
    f"{CHAT_AGENT_MODULE}.orchestrator.confirmation.confirmation_guardrails",
    f"{CHAT_AGENT_MODULE}.orchestrator.confirmation.confirmation_models",
    f"{CHAT_AGENT_MODULE}.orchestrator.confirmation.confirmation_phrases",
    f"{CHAT_AGENT_MODULE}.orchestrator.confirmation.affirmation",
    ".".join(("apps", "chat", "src", "schedulers")),
    ".".join(("banking", "knowledge")),
)

DELETED_PACKAGE_PATHS = (
    "/".join(("apps", "chat", "src", "agent", "protocols.py")),
    "/".join(("apps", "chat", "src", "agent", "shared")),
    "/".join(("apps", "chat", "src", "agent", "workers")),
    "/".join(("apps", "chat", "src", "agent", "workers", "faq")),
    "/".join(("apps", "chat", "src", "agent", "workers", "account")),
    "/".join(("apps", "chat", "src", "agent", "workers", "beneficiary")),
    "/".join(("apps", "chat", "src", "agent", "workers", "onboarding")),
    "/".join(("apps", "chat", "src", "agent", "workers", "__shared__")),
    "/".join(("apps", "chat", "src", "agent", "workers", "airtime")),
    "/".join(("apps", "chat", "src", "agent", "workers", "data")),
    "/".join(("apps", "chat", "src", "agent", "workers", "transfer")),
    "/".join(("apps", "chat", "src", "agent", "workers", "support")),
    "/".join(("apps", "chat", "src", "agent", "workers", "query")),
    "/".join(("apps", "chat", "src", "agent", "orchestrator", "confirmation", "confirmation_classifier.py")),
    "/".join(("apps", "chat", "src", "agent", "orchestrator", "confirmation", "confirmation_guardrails.py")),
    "/".join(("apps", "chat", "src", "agent", "orchestrator", "confirmation", "confirmation_models.py")),
    "/".join(("apps", "chat", "src", "agent", "orchestrator", "confirmation", "confirmation_phrases.py")),
    "/".join(("apps", "chat", "src", "agent", "orchestrator", "confirmation", "__init__.py")),
    "/".join(
        ("apps", "chat", "src", "agent", "orchestrator", "confirmation", "affirmation", "__init__.py")
    ),
    "/".join(
        ("apps", "chat", "src", "agent", "orchestrator", "confirmation", "affirmation", "service.py")
    ),
    "/".join(("apps", "chat", "src", "schedulers")),
    "/".join(("banking", "knowledge")),
)


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root_name in SOURCE_ROOTS:
        root = ROOT / root_name
        if root.exists():
            files.extend(root.rglob("*.py"))
    return sorted(files)


def _path_has_python_sources(path: Path) -> bool:
    if path.is_file():
        return path.suffix == ".py"
    if path.is_dir():
        return any(child.is_file() and child.suffix == ".py" for child in path.rglob("*.py"))
    return False


def _is_forbidden_module(module: str) -> bool:
    return any(module == forbidden or module.startswith(f"{forbidden}.") for forbidden in MOVED_WORKER_MODULES)


def _is_chat_app_module(module: str) -> bool:
    return module == "apps.chat" or module.startswith("apps.chat.")


def _import_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_module(alias.name):
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_forbidden_module(module):
                violations.append(module)
            for alias in node.names:
                imported = f"{module}.{alias.name}" if module else alias.name
                if _is_forbidden_module(imported):
                    violations.append(imported)
    return violations


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return modules


def test_moved_worker_packages_do_not_exist_under_chat_or_knowledge() -> None:
    existing = [path for package in DELETED_PACKAGE_PATHS if _path_has_python_sources(path := ROOT / package)]

    assert existing == []


def test_moved_workers_are_not_imported_through_old_paths() -> None:
    this_file = Path(__file__).resolve()
    violations: list[str] = []
    for path in _python_files():
        if path.resolve() == this_file:
            continue
        for module in _import_violations(path):
            violations.append(f"{path.relative_to(ROOT)} imports {module}")

    assert violations == []


def test_moved_worker_old_paths_do_not_appear_in_python_sources() -> None:
    this_file = Path(__file__).resolve()
    violations: list[str] = []
    for path in _python_files():
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        for module in MOVED_WORKER_MODULES:
            if module in text:
                violations.append(f"{path.relative_to(ROOT)} references {module}")

    assert violations == []


def test_banking_modules_do_not_import_chat_app_internals() -> None:
    banking_root = ROOT / "banking"
    violations: list[str] = []
    for path in sorted(banking_root.rglob("*.py")):
        for module in _imported_modules(path):
            if _is_chat_app_module(module):
                violations.append(f"{path.relative_to(ROOT)} imports {module}")

    assert violations == []
