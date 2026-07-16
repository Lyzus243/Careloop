import os
from dotenv import load_dotenv
import re

if os.path.exists(".env"):
    load_dotenv(dotenv_path=".env", override=True)

import resend
import logging

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = os.getenv("APP_BASE_URL", "https://careloop.dpdns.org")


def _html_to_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class EmailService:
    def __init__(self):
        self.api_key = os.getenv("RESEND_API_KEY")
        self.from_email = os.getenv("RESEND_FROM_EMAIL", "hello@careloop.dpdns.org")
        self.from_name = os.getenv("SENDGRID_FROM_NAME", "Careloop")
        self.reply_to = os.getenv("RESEND_REPLY_TO", self.from_email)
        if not self.api_key:
            print("WARNING: Resend API key not found")
        else:
            resend.api_key = self.api_key
            print("Resend loaded successfully")

    def _send(self, to_email: str, subject: str, html: str, text: str = None) -> bool:
        try:
            params = {
                "from": f"{self.from_name} <{self.from_email}>",
                "to": [to_email],
                "subject": subject,
                "html": html,
                "text": text or _html_to_text(html),
                "reply_to": self.reply_to,
            }
            response = resend.Emails.send(params)
            logger.info(f"Email sent to {to_email}: {response}")
            print(f"Email sent to {to_email}")
            return True
        except Exception as e:
            print(f"EMAIL ERROR: {type(e).__name__}: {e}")
            logger.error(f"Failed to send email: {e}")
            return False

    async def send_verification_email(self, email: str, token: str, base_url: str = DEFAULT_BASE_URL, name: str = "") -> bool:
        html, text = self._get_verification_email_template(token, base_url, name)
        return self._send(email, "Confirm your Careloop email address", html, text)

    async def send_password_reset_email(self, email: str, token: str, base_url: str = DEFAULT_BASE_URL) -> bool:
        html, text = self._get_password_reset_email_template(token, base_url)
        return self._send(email, "Reset your Careloop password", html, text)

    async def send_welcome_email(self, email: str, name: str, base_url: str = DEFAULT_BASE_URL) -> bool:
        html, text = self._get_welcome_email_template(name, base_url)
        return self._send(email, "Your Careloop account is ready", html, text)

    def _get_verification_email_template(self, token: str, base_url: str, name: str = ""):
        verification_url = f"{base_url}/verify-email?token={token}"
        greeting = f"Hi {name}," if name else "Hi there,"
        html = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:32px 24px;color:#333;line-height:1.6;">
        <p style="font-size:16px;">{greeting}</p>
        <p style="font-size:15px;">Thanks for creating a Careloop account. Please confirm your email address by clicking the link below.</p>
        <p style="text-align:center;margin:32px 0;">
            <a href="{verification_url}" style="background:#3333FF;color:white;padding:14px 32px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:600;display:inline-block;">Confirm email address</a>
        </p>
        <p style="font-size:14px;color:#666;">Or copy and paste this link into your browser:</p>
        <p style="font-size:13px;color:#3333FF;word-break:break-all;">{verification_url}</p>
        <p style="font-size:13px;color:#999;margin-top:32px;">This link expires in 24 hours. If you didn't create a Careloop account, you can ignore this email.</p>
        <p style="font-size:14px;margin-top:24px;">Thanks,<br>The Careloop Team</p>
        </body></html>
        """
        text = (
            f"{greeting}\n\n"
            "Thanks for creating a Careloop account. Confirm your email address using the link below:\n\n"
            f"{verification_url}\n\n"
            "This link expires in 24 hours. If you didn't create a Careloop account, you can ignore this email.\n\n"
            "Thanks,\nThe Careloop Team"
        )
        return html, text

    def _get_password_reset_email_template(self, token: str, base_url: str):
        reset_url = f"{base_url}/reset-password?token={token}"
        html = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:32px 24px;color:#333;line-height:1.6;">
        <p style="font-size:16px;">Hi there,</p>
        <p style="font-size:15px;">We received a request to reset your Careloop password. Click the link below to choose a new one:</p>
        <p style="text-align:center;margin:32px 0;">
            <a href="{reset_url}" style="background:#3333FF;color:white;padding:14px 32px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:600;display:inline-block;">Reset password</a>
        </p>
        <p style="font-size:14px;color:#666;">Or copy and paste this link into your browser:</p>
        <p style="font-size:13px;color:#3333FF;word-break:break-all;">{reset_url}</p>
        <p style="font-size:13px;color:#999;margin-top:32px;">This link expires in 1 hour. If you didn't request a password reset, you can ignore this email.</p>
        <p style="font-size:14px;margin-top:24px;">Thanks,<br>The Careloop Team</p>
        </body></html>
        """
        text = (
            "Hi there,\n\n"
            "We received a request to reset your Careloop password. Use the link below to choose a new one:\n\n"
            f"{reset_url}\n\n"
            "This link expires in 1 hour. If you didn't request a password reset, you can ignore this email.\n\n"
            "Thanks,\nThe Careloop Team"
        )
        return html, text

    def _get_welcome_email_template(self, name: str, base_url: str):
        first_name = name or "there"
        dashboard_url = f"{base_url}/dashboard"
        html = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:32px 24px;color:#333;line-height:1.6;">
        <p style="font-size:16px;">Hi {first_name},</p>
        <p style="font-size:15px;">Your Careloop account is ready. Start managing your customer relationships today.</p>
        <p style="text-align:center;margin:32px 0;">
            <a href="{dashboard_url}" style="background:#3333FF;color:white;padding:14px 32px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:600;display:inline-block;">Open your dashboard</a>
        </p>
        <p style="font-size:13px;color:#999;">If you have questions, just reply to this email.</p>
        <p style="font-size:14px;margin-top:24px;">Thanks,<br>The Careloop Team</p>
        </body></html>
        """
        text = (
            f"Hi {first_name},\n\n"
            "Your Careloop account is ready. Start managing your customer relationships today.\n\n"
            f"Open your dashboard: {dashboard_url}\n\n"
            "If you have questions, just reply to this email.\n\n"
            "Thanks,\nThe Careloop Team"
        )
        return html, text



    async def send_birthday_reminder(self, owner_email: str, customer_name: str, owner_name: str = "there") -> bool:
        html = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:32px 24px;color:#333;line-height:1.6;">
        <p style="font-size:16px;">Hi {owner_name},</p>
        <p style="font-size:15px;">Just a heads up - today is <strong>{customer_name}'s</strong> birthday! 🎂</p>
        <p style="font-size:15px;">This is a great opportunity to reach out and strengthen your relationship with them.</p>
        <p style="font-size:14px;color:#666;">Log in to Careloop to send them a message.</p>
        <p style="font-size:14px;margin-top:24px;">Thanks,<br>The Careloop Team</p>
        </body></html>
        """
        text = (
            f"Hi {owner_name},\n\n"
            f"Just a heads up - today is {customer_name}'s birthday!\n\n"
            "This is a great opportunity to reach out and strengthen your relationship with them.\n\n"
            "Thanks,\nThe Careloop Team"
        )
        return self._send(owner_email, f"🎂 {customer_name} has a birthday today!", html, text)

email_service = EmailService()
