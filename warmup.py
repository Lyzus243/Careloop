"""
Email Warmup Script for careloop.dpdns.org
Run daily: python warmup.py
Sends 2 emails per day to build Gmail reputation.
"""

import resend
import os
import json
import datetime
from dotenv import load_dotenv

load_dotenv(override=True)
resend.api_key = os.getenv("RESEND_API_KEY")

FROM_EMAIL = "hello@careloop.dpdns.org"
FROM_NAME = "Careloop"

RECIPIENTS = [
    "talktolawrenceibolo@gmail.com",
    "adewonisekayode@gmail.com",
    "blessedolubiyi40@gmail.com",
]

WARMUP_LOG = "warmup_log.json"
DAILY_LIMIT = 2


def load_log():
    if os.path.exists(WARMUP_LOG):
        with open(WARMUP_LOG) as f:
            return json.load(f)
    return {"days": 0, "total_sent": 0, "history": []}


def save_log(log):
    with open(WARMUP_LOG, "w") as f:
        json.dump(log, f, indent=2)


def send_warmup_email(to_email: str, day: int) -> bool:
    subjects = [
        "Checking in from Careloop",
        "A quick note from the Careloop team",
        "Your Careloop account update",
        "Careloop: Tips for managing customers",
        "Following up from Careloop",
    ]
    subject = subjects[day % len(subjects)]

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:24px;color:#333;line-height:1.6;">
    <p>Hi there,</p>
    <p>This is a friendly note from the Careloop team. We hope you're enjoying using Careloop to manage your customer relationships.</p>
    <p>If you have any questions or feedback, just reply to this email - we would love to hear from you.</p>
    <p>Thanks,<br>The Careloop Team</p>
    <p style="font-size:12px;color:#999;">You're receiving this because you signed up for Careloop.</p>
    </body></html>
    """

    text = (
        "Hi there,\n\n"
        "This is a friendly note from the Careloop team. We hope you're enjoying using Careloop "
        "to manage your customer relationships.\n\n"
        "If you have any questions or feedback, just reply to this email - we'd love to hear from you.\n\n"
        "Thanks,\nThe Careloop Team"
    )

    try:
        params = {
            "from": f"{FROM_NAME} <{FROM_EMAIL}>",
            "to": [to_email],
            "subject": subject,
            "html": html,
            "text": text,
        }
        response = resend.Emails.send(params)
        print(f"  Sent to {to_email}: {response.get('id')}")
        return True
    except Exception as e:
        print(f"  Failed to send to {to_email}: {e}")
        return False


def run_warmup():
    log = load_log()
    today = datetime.date.today().isoformat()
    log["days"] += 1
    day = log["days"]

    # Rotate through recipients, picking 2 per day
    start = ((day - 1) * DAILY_LIMIT) % len(RECIPIENTS)
    recipients_today = []
    for i in range(DAILY_LIMIT):
        recipients_today.append(RECIPIENTS[(start + i) % len(RECIPIENTS)])

    print(f"\n{'='*50}")
    print(f"Warmup Day {day} - {today}")
    print(f"Sending to {len(recipients_today)} recipient(s)")
    print(f"{'='*50}")

    sent = 0
    for email in recipients_today:
        if send_warmup_email(email, day):
            sent += 1

    log["total_sent"] += sent
    log["history"].append({
        "date": today,
        "day": day,
        "sent": sent,
        "recipients": recipients_today
    })
    save_log(log)

    print(f"\nDone. Sent {sent}/{len(recipients_today)} emails today.")
    print(f"Total sent so far: {log['total_sent']}")
    print(f"\nReminder: Ask recipients to OPEN the email and mark as 'Not Spam'!")


if __name__ == "__main__":
    run_warmup()
