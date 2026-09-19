import os
from typing import Dict, Any
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()


class OpenAIService:
    """Service for generating AI-powered messages using OpenAI API"""

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")

        if not self.api_key:
            print("WARNING: OPENAI_API_KEY not found. AI-generated messages will use fallback text.")
            self.client = None
        else:
            self.client = AsyncOpenAI(api_key=self.api_key)

    async def generate_follow_up_message(
        self,
        customer: Dict[str, Any],
        user_name: str,
        user_business: str
    ) -> str:

        customer_type = customer.get('customer_type', 'new').lower()

        base_prompt = f"""
        You are a professional business assistant for {user_name} at {user_business}.
        
        Customer Name: {customer.get('name', 'Valued Customer')}
        Type: {customer_type}
        Business: {user_business}

        Write a short friendly follow-up message under 160 characters.
        """

        try:
            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You write short business SMS messages."},
                    {"role": "user", "content": base_prompt}
                ],
                max_tokens=150,
                temperature=0.7
            )

            return response.choices[0].message.content.strip()

        except Exception:
            return f"Hi {customer.get('name','there')}! This is {user_name} from {user_business} 😊"

    async def generate_sales_message(
        self,
        customer: Dict[str, Any],
        product: str,
        user_name: str,
        user_business: str
    ) -> str:

        try:
            response = await self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You write short sales SMS messages."},
                    {"role": "user", "content": f"Write SMS promoting {product} to {customer.get('name','customer')}."}
                ],
                max_tokens=150,
                temperature=0.8
            )

            return response.choices[0].message.content.strip()

        except Exception:
            return f"Hi {customer.get('name','there')}! Check out {product} 🎯"


# Singleton instance
openai_service = OpenAIService()
