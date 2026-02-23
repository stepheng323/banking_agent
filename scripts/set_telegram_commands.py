import asyncio
from shared.clients.telegram.client import TelegramClient

async def main():
    client = TelegramClient()
    commands = [
        {"command": "start", "description": "Restart the bot and view the main menu"},
        {"command": "balance", "description": "Check your account balances"},
        {"command": "transfer", "description": "Start a new transfer"},
        {"command": "airtime", "description": "Buy airtime or data"},
        {"command": "support", "description": "Contact customer service"}
    ]
    
    success = await client.set_my_commands(commands)
    if success:
        print("\n✅ Successfully registered the persistent Telegram Bot Menu!")
    else:
        print("\n❌ Failed to register commands.")

if __name__ == "__main__":
    asyncio.run(main())
