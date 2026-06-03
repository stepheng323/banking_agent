"""Runtime factory for FAQ-domain workers."""

from typing import Any

from banking.faq.worker import FAQWorker


def build_faq_worker(*, llm: Any, get_db: Any) -> FAQWorker:
    """Build the FAQ worker through the FAQ domain boundary."""
    return FAQWorker(llm=llm, get_db=get_db)
