"""Initialize LangGraph checkpoint tables in PostgreSQL."""

import sys

from langgraph.checkpoint.postgres import PostgresSaver


def init_checkpoint_tables() -> None:
    """
    Initialize LangGraph checkpoint tables in PostgreSQL.

    This creates the necessary tables for storing conversation state,
    allowing multi-turn conversations to persist across restarts.
    """
    try:
        import os

        db_url = os.getenv("DATABASE_URL", "")

        if not db_url:
            raise ValueError("DATABASE_URL environment variable is not set")

        print("🔄 Initializing LangGraph checkpoint tables...")

        # Create checkpointer instance
        checkpointer = PostgresSaver.from_conn_string(conn_string=db_url)

        # Create the checkpoint tables
        checkpointer.setup()

        print("✓ LangGraph checkpoint tables initialized successfully")
        print("   Tables created:")
        print("   - checkpoints: Stores conversation state snapshots")
        print("   - checkpoint_writes: Stores pending writes")

    except Exception as e:
        print(f"❌ Failed to initialize checkpoint tables: {e}")
        raise


if __name__ == "__main__":
    """Run as standalone script."""
    try:
        init_checkpoint_tables()
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
