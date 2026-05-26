"""CLI for hybrid readiness transcript automation."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from scripts.readiness_runner import run_readiness_sync


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run banking-agent readiness transcript scenarios.")
    parser.add_argument("--mode", default="deterministic", choices=("deterministic", "dry-run"))
    parser.add_argument(
        "--scenario",
        default="all",
        choices=(
            "all",
            "core",
            "transfer",
            "data",
            "airtime",
            "unsupported",
            "schedule",
            "quick",
            "mvp",
            "query",
            "query-deep",
        ),
    )
    parser.add_argument("--phone", help="Existing test user's phone number. Required for --mode dry-run.")
    parser.add_argument("--channel", default="telegram", choices=("telegram", "whatsapp"))
    parser.add_argument("--channel-user-id", default=None, help="Optional channel user id for dry-run mode.")
    parser.add_argument("--seed", action="store_true", help="Seed standard test data before dry-run mode.")
    parser.add_argument(
        "--reset-session",
        action="store_true",
        help="Delete Redis session/checkpoint keys before dry-run.",
    )
    parser.add_argument("--stop-on-fail", action="store_true", help="Stop after the first failed turn.")
    parser.add_argument("--json-output", help="Optional path to write a JSON readiness report.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    raise SystemExit(
        run_readiness_sync(
            mode=args.mode,
            scenario=args.scenario,
            phone=args.phone,
            channel=args.channel,
            channel_user_id=args.channel_user_id,
            seed=args.seed,
            reset_session=args.reset_session,
            stop_on_fail=args.stop_on_fail,
            json_output=args.json_output,
        )
    )


if __name__ == "__main__":
    main()
