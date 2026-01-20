import asyncio

from pydantic import BaseModel, Field


# Mock Types
class TransferConfirmation(BaseModel):
    token: str | None = None
    summary: str | None = None
    confirmed: bool = False


class TransferPayload(BaseModel):
    amount: float | None = 5000.0
    recipient_name: str | None = "Tolu"
    recipient_resolved_name: str | None = "TOLU ADEDAYO"
    recipient_account: str | None = "0760505261"
    recipient_bank_name: str | None = "Opay"
    confirmation: TransferConfirmation = Field(default_factory=TransferConfirmation)
    idempotency_key: str | None = "key-123"
    funding_plan: dict | None = None
    source_account_id: str | None = "src-1"


class TransferGates(BaseModel):
    pin_verified: bool = False
    confirmation_confirmed: bool = False


class TransferResult(BaseModel):
    outcome: str
    patch: dict | None = None
    receipt: dict | None = None


# Mock Worker (simplified)
class TransferWorker:
    async def run(self, payload: dict, context: dict, user_message=None, pin_verified=False):
        data = TransferPayload(**payload)
        gates = TransferGates(pin_verified=pin_verified, confirmation_confirmed=data.confirmation.confirmed)

        print(f"DEBUG: Gates - Pin: {gates.pin_verified}, Confirmed: {gates.confirmation_confirmed}")

        # 1. Resolve (Skipped for repro)

        # 2. Confirmation Check
        # Call build_confirmation (Mocked)
        res = TransferResult(outcome="needs_confirmation", patch={"confirmation": {"summary": "foo"}})

        if not gates.confirmation_confirmed:
            print("RETURNING NEEDS_CONFIRMATION")
            return self.with_key(res, "key-123", data)

        print("PASSED CONFIRMATION CHECK")

        # 3. Auth Check
        # require_auth
        auth_res = TransferResult(outcome="ok" if gates.pin_verified else "needs_auth")
        if auth_res.outcome != "ok":
            print("RETURNING NEEDS_AUTH")
            return self.with_key(auth_res, "key-123", data)

        print("PASSED AUTH CHECK")

        # 4. Execution
        print("EXECUTING...")
        return TransferResult(outcome="ok", receipt={"status": "queued"})

    def with_key(self, res, key, data):
        if not res.patch:
            res.patch = {}
        # Simulate my fix
        res.patch.update(data.model_dump())
        res.patch["idempotency_key"] = key
        return res


async def main():
    worker = TransferWorker()

    # Scenario: User enters PIN for Confirmation
    # Orchestrator updates payload: confirmed=True
    payload = {"amount": 5000, "confirmation": {"confirmed": True}, "idempotency_key": "key-123"}

    # And passes pin_verified=True
    print("--- Test Run ---")
    result = await worker.run(payload, {}, pin_verified=True)
    print(f"Result Outcome: {result.outcome}")


if __name__ == "__main__":
    asyncio.run(main())
