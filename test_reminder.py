from dotenv import load_dotenv
load_dotenv(dotenv_path=r"C:\Users\User\Careloop\.env")

import asyncio
from app.services.email_service import email_service

async def main():
    result = await email_service.send_birthday_reminder(
        to_email="testuser@careloop.dev",
        customer_name="Joshua Olaniran",
        owner_name="Test User"
    )
    print("Success:", result)

asyncio.run(main())
