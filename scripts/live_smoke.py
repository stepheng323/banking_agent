"""Compatibility wrapper for the readiness dry-run runner.

Usage:
    PYTHONPATH=. uv run --extra all python -m scripts.live_smoke --phone 2348162511023 --seed
    PYTHONPATH=. uv run --extra all python -m scripts.live_smoke --phone 2348162511023 --scenario mvp
    PYTHONPATH=. uv run --extra all python -m scripts.live_smoke --phone 2348162511023 --scenario query-deep
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from scripts.readiness_runner import run_readiness_sync


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an automated local live-dry-run MVP smoke test.")
    parser.add_argument("--phone", required=True, help="Existing test user's phone number.")
    parser.add_argument("--channel", default="telegram", choices=("telegram", "whatsapp"))
    parser.add_argument(
        "--channel-user-id",
        default=None,
        help="Optional channel user id to attach to message context.",
    )
    parser.add_argument("--scenario", default="quick", choices=("quick", "mvp", "query", "query-deep"))
    parser.add_argument("--seed", action="store_true", help="Seed standard accounts and Tolu beneficiaries first.")
    parser.add_argument(
        "--reset-session",
        action="store_true",
        help="Delete Redis session/checkpoint keys for this phone/channel.",
    )
    parser.add_argument("--stop-on-fail", action="store_true", help="Stop after the first failed assertion.")
    parser.add_argument("--json-output", help="Optional path to write a JSON readiness report.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    raise SystemExit(
        run_readiness_sync(
            mode="dry-run",
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
