import os
from dotenv import load_dotenv
if os.path.exists(".env"):
    load_dotenv(dotenv_path=".env", override=True)
import logging
import resend

logger = logging.getLogger(__name__)

import base64 as _b64
import os as _os

_LOGO_PATH = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "Frontend", "assets", "primary_logo.jpg")
try:
    with open(_LOGO_PATH, "rb") as _logo_file:
        CARELOOP_LOGO_B64 = _b64.b64encode(_logo_file.read()).decode("utf-8")
except Exception:
    CARELOOP_LOGO_B64 = ""

class EmailService:
    def __init__(self):
        self.api_key = os.getenv("RESEND_API_KEY")
        self.from_email = os.getenv("RESEND_FROM_EMAIL", "Careloop <noreply@mycareloop.com.ng>")
        if not self.api_key:
            print("WARNING: RESEND_API_KEY not found")
        else:
            resend.api_key = self.api_key
            print("Resend loaded successfully")

    def _send(self, to_email: str, subject: str, html: str) -> bool:
        try:
            params = {
                "from": self.from_email,
                "to": [to_email],
                "subject": subject,
                "html": html,
            }
            result = resend.Emails.send(params)
            logger.info(f"Email sent to {to_email}: {result}")
            print(f"RESEND: Email successfully sent to {to_email} (id={result.get('id') if isinstance(result, dict) else result})")
            return True
        except Exception as e:
            print(f"RESEND ERROR: {type(e).__name__}: {e}")
            logger.error(f"Failed to send email via Resend: {e}")
            return False

    async def send_verification_email(self, email: str, token: str, base_url: str = "https://mycareloop.com.ng") -> bool:
        html = self._get_verification_email_template(token, base_url)
        return self._send(email, "Your Careloop verification link", html)

    async def send_password_reset_email(self, email: str, token: str, base_url: str = "https://mycareloop.com.ng") -> bool:
        html = self._get_password_reset_email_template(token, base_url)
        return self._send(email, "Reset your Careloop password", html)

    async def send_welcome_email(self, email: str, name: str, base_url: str = "https://mycareloop.com.ng") -> bool:
        html = self._get_welcome_email_template(name, base_url)
        return self._send(email, "Welcome to Careloop!", html)

    def _get_verification_email_template(self, token: str, base_url: str = "https://mycareloop.com.ng", name: str = "") -> str:
        verification_url = f"{base_url}/verify-email?token={token}"
        greeting = f"Hi {name}," if name else "Hi there,"
        return f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:32px 24px;color:#333;line-height:1.6;">
        <p style="font-size:16px;">{greeting}</p>
        <p style="font-size:15px;">Thanks for creating a Careloop account. Please verify your email address by clicking the button below.</p>
        <p style="text-align:center;margin:32px 0;">
            <a href="{verification_url}" style="background:#3333FF;color:white;padding:14px 32px;text-decoration:none;border-radius:6px;font-size:15px;font-weight:600;display:inline-block;">Verify Email Address</a>
        </p>
        <p style="font-size:14px;color:#666;">Or copy and paste this link into your browser:</p>
        <p style="font-size:13px;color:#3333FF;word-break:break-all;">{verification_url}</p>
        <p style="font-size:13px;color:#999;margin-top:32px;">This link expires in 24 hours. If you didn't create a Careloop account, you can safely ignore this email.</p>
        <p style="font-size:14px;margin-top:24px;">Thanks,<br><strong>The Careloop Team</strong></p>
        </body></html>
        """

    def _get_password_reset_email_template(self, token: str, base_url: str = "https://mycareloop.com.ng") -> str:
        reset_url = f"{base_url}/reset-password?token={token}"
        return f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
        <h2>Reset your Careloop password</h2>
        <p>Hi there,</p>
        <p>We received a request to reset your password. Click the link below to create a new one:</p>
        <p><a href="{reset_url}" style="background:#3333FF;color:white;padding:12px 24px;text-decoration:none;border-radius:5px;display:inline-block;">Reset Password</a></p>
        <p>Or copy and paste this link into your browser:</p>
        <p>{reset_url}</p>
        <p>This link expires in 1 hour.</p>
        <p>If you didn't request a password reset, please ignore this email.</p>
        <p>Thanks,<br>The Careloop Team</p>
        </body></html>
        """

    def _get_welcome_email_template(self, name: str, base_url: str = "https://mycareloop.com.ng") -> str:
        """Get HTML template for welcome email"""
        dashboard_url = f"{base_url}/careloop-dashboard.html"
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Welcome to Careloop!</title>
        </head>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: #f8f9fa; padding: 30px; border-radius: 10px;">
                <h1 style="color: #3333FF; margin-bottom: 20px;">Welcome to Careloop, {name}! 👋</h1>
                <p style="color: #666; line-height: 1.6;">Thank you for joining Careloop! Your account has been successfully created and you're ready to start managing your customer relationships more efficiently.</p>
                
                <div style="background: white; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <h3 style="color: #333; margin-bottom: 15px;">What's next?</h3>
                    <ul style="color: #666; line-height: 1.8;">
                        <li>Add your first customers to get started</li>
                        <li>Set up follow-up reminders</li>
                        <li>Track sales and revenue</li>
                        <li>Generate detailed reports</li>
                    </ul>
                </div>
                
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{dashboard_url}" style="background: #3333FF; color: white; padding: 12px 30px; text-decoration: none; border-radius: 5px; display: inline-block;">Go to Dashboard</a>
                </div>
                
                <hr style="border: none; border-top: 1px solid #eee; margin: 30px 0;">
                <p style="color: #999; font-size: 12px;">If you have any questions, feel free to reach out to our support team.</p>
            </div>
        </body>
        </html>
        """

    def send_followup_email(self, to_email: str, customer_name: str, business_name: str) -> bool:
        subject = f"Hey {customer_name}, just thinking of you 😊"
        html = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:32px 24px;color:#333;line-height:1.8;">
        <p style="font-size:16px;">Hi {customer_name},</p>
        <p style="font-size:15px;">Hope you are doing well! We just wanted to check in and let you know that we genuinely appreciate your support of <strong>{business_name}</strong>.</p>
        <p style="font-size:15px;">If there is anything you need, any questions you have, or anything we can do better for you — we are always here and happy to help.</p>
        <p style="font-size:15px;">We value you not just as a customer, but as someone whose trust means a lot to us. Thank you for being part of our journey.</p>
        <p style="font-size:15px;">Warmly,<br><strong>{business_name}</strong></p>
        </body></html>
        """
        return self._send(to_email, subject, html)

    def render_birthday_email_html(self, customer_name: str, business_name: str) -> tuple[str, str]:
        """Build the birthday email subject and HTML without sending. Used for both sending and previewing."""
        subject = f"Happy Birthday {customer_name}!"
        html = f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
          <tr>
            <td style="height:4px;background:#4F46E5;line-height:4px;font-size:1px;">&nbsp;</td>
          </tr>
          <tr>
            <td style="padding:44px 36px 8px;">
              <div style="font-size:13px;font-weight:600;color:#4F46E5;letter-spacing:0.6px;text-transform:uppercase;margin-bottom:14px;">
                It's your birthday
              </div>
              <div style="font-size:24px;font-weight:700;color:#111111;line-height:1.3;margin-bottom:20px;letter-spacing:-0.4px;">
                Happy Birthday, {customer_name}
              </div>
              <p style="font-size:15px;color:#4b5563;line-height:1.7;margin:0 0 16px;">
                Hi {customer_name},
              </p>
              <p style="font-size:15px;color:#4b5563;line-height:1.7;margin:0 0 16px;">
                We want to take a moment on your birthday to say a big thank you. Your support means a great deal to us all at <strong style="color:#1a1a1a;">{business_name}</strong>, and We most certainly do not take it for granted.
              </p>
              <p style="font-size:15px;color:#4b5563;line-height:1.7;margin:0 0 32px;">
                We sincerly  hope you have a wonderful day, however you choose to spend it.
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 36px 40px;">
              <p style="font-size:15px;color:#4b5563;line-height:1.7;margin:0;">
                Best,<br>
                <strong style="color:#1a1a1a;">{business_name}</strong>
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 36px;background:#fafafa;border-top:1px solid #f0f0f0;">
              <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td style="font-size:11px;color:#b0b3b9;vertical-align:middle;">
                    Sent with
                  </td>
                  <td style="width:70px;vertical-align:middle;padding-left:6px;">
                    <img src="data:image/jpeg;base64,{CARELOOP_LOGO_B64}" alt="Careloop" style="height:16px;width:auto;display:block;opacity:0.55;">
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
        """
        return subject, html

    def send_birthday_email(self, to_email: str, customer_name: str, business_name: str) -> bool:
        subject, html = self.render_birthday_email_html(customer_name, business_name)
        return self._send(to_email, subject, html)

    def render_birthday_reminder_html(self, customer_name: str, owner_name: str, base_url: str = "https://mycareloop.com.ng") -> tuple[str, str]:
        """Build the birthday reminder subject and HTML without sending. Used for both sending and previewing."""
        dashboard_url = f"{base_url}/careloop-dashboard.html"
        subject = f"It's {customer_name}'s birthday today!"
        html = f"""
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
          <tr>
            <td style="height:4px;background:#4F46E5;line-height:4px;font-size:1px;">&nbsp;</td>
          </tr>
          <tr>
            <td style="padding:40px 36px 8px;">
              <div style="font-size:13px;font-weight:600;color:#4F46E5;letter-spacing:0.6px;text-transform:uppercase;margin-bottom:14px;">
                Birthday reminder
              </div>
              <div style="font-size:22px;font-weight:700;color:#111111;line-height:1.3;margin-bottom:20px;letter-spacing:-0.4px;">
                It's {customer_name}'s birthday today
              </div>
              <p style="font-size:15px;color:#4b5563;line-height:1.7;margin:0 0 16px;">
                Hi {owner_name},
              </p>
              <p style="font-size:15px;color:#4b5563;line-height:1.7;margin:0 0 28px;">
                Just a heads up — today is {customer_name}'s birthday. Head to your Careloop dashboard to send them a birthday email.
              </p>
              <table role="presentation" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="border-radius:6px;background:#4F46E5;">
                    <a href="{dashboard_url}" style="display:inline-block;padding:12px 24px;font-size:14px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:6px;">
                      Open Dashboard
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 36px;background:#fafafa;border-top:1px solid #f0f0f0;">
              <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td style="font-size:11px;color:#b0b3b9;vertical-align:middle;">
                    Sent with
                  </td>
                  <td style="width:70px;vertical-align:middle;padding-left:6px;">
                    <img src="data:image/jpeg;base64,{CARELOOP_LOGO_B64}" alt="Careloop" style="height:16px;width:auto;display:block;opacity:0.55;">
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
        """
        return subject, html

    async def send_birthday_reminder(self, to_email: str, customer_name: str, owner_name: str, base_url: str = "https://mycareloop.com.ng") -> bool:
        subject, html = self.render_birthday_reminder_html(customer_name, owner_name, base_url)
        return self._send(to_email, subject, html)

    def send_bulk_email(self, to_email: str, customer_name: str, subject: str, message: str, business_name: str) -> bool:
        html = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:32px 24px;color:#333;line-height:1.8;">
        <p style="font-size:16px;">Hi {customer_name},</p>
        <p style="font-size:15px;">{message}</p>
        <p style="font-size:14px;color:#999;margin-top:32px;">Sent with care by <strong>{business_name}</strong> via Careloop.</p>
        </body></html>
        """
        return self._send(to_email, subject, html)

# Create a singleton instance
email_service = EmailService()
