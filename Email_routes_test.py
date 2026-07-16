import requests
import json
import random
import string

BASE_URL = "http://localhost:8001"

def random_string(n=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n))

# --- Test 1: Signup ---
test_email = f"test_{random_string()}@gmail.com"
signup_payload = {
    "email": test_email,
    "password": "TestPassword123!",
    "full_name": "Signup Test User",
    "business_name": "Test Business",
    "phone_number": "+2348100000000"
}

print("="*50)
print("TEST 1: Signup")
print("="*50)
print(f"Using test email: {test_email}")
r = requests.post(f"{BASE_URL}/api/auth/signup", json=signup_payload)
print("Status code:", r.status_code)
try:
    print(json.dumps(r.json(), indent=2))
except Exception:
    print(r.text)

if r.status_code == 201:
    print("\nPASS: signup returned 201. Check Resend dashboard for a verification email to:", test_email)
else:
    print("\nFAIL: signup did not return 201")

# --- Test 2: Forgot Password ---
existing_verified_email = "oluwaseunolaniran01@gmail.com"

print("\n" + "="*50)
print("TEST 2: Forgot Password")
print("="*50)
print(f"Using existing verified account: {existing_verified_email}")
r = requests.post(f"{BASE_URL}/api/auth/forgot-password", json={"email": existing_verified_email})
print("Status code:", r.status_code)
try:
    print(json.dumps(r.json(), indent=2))
except Exception:
    print(r.text)

if r.status_code == 200:
    print("\nPASS: forgot-password returned 200. Check Resend dashboard for a reset email to:", existing_verified_email)
else:
    print("\nFAIL: forgot-password did not return 200")

print("\n" + "="*50)
print("Both requests sent. Check http://resend.com/emails for delivery status on both.")
print("="*50)