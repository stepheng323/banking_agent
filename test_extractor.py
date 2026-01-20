import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from langchain_openai import ChatOpenAI

from apps.core.src.agent.graphs.transfer.extractor import TransferEntityExtractor


async def test_extractor():
    print("Initializing extractor...")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    extractor = TransferEntityExtractor(llm)

    msg = "Kuda 1234567890 opay"
    print(f"Extracting from: '{msg}'")

    try:
        result = await extractor.extract(msg)
        print("Extraction Result:")
        print(result.model_dump_json(indent=2))
    except Exception as e:
        print(f"Extraction Failed: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_extractor())
