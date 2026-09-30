#!/usr/bin/env python3
"""Checks phone normalisation and both customer import endpoints against an in-memory SQLite database.

Run from the repo root: python3 test_customer_import.py
"""
import asyncio, io, os, sys
sys.path.insert(0, os.getcwd())
os.environ.setdefault("SECRET_KEY", "x")
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from app.models.base import Base
import app.models  # noqa: registers models
from app.models.customer import Customer
from app.database import get_db
from app.dependencies import get_current_user_id
from app.routes import customer as customer_routes
from app.utils.phone import normalize_phone

# --- phone normaliser ---
cases = {
    ("0803 123 4567", "+234"): "+2348031234567",
    ("+234 803 123 4567", "+234"): "+2348031234567",
    ("234-803-123-4567", "+234"): "+2348031234567",
    ("+2340803 123 4567", "+234"): "+2348031234567",
    ("8031234567", "+234"): "+2348031234567",
    ("8031234567.0", "+234"): "+2348031234567",
    ("002348031234567", "+234"): "+2348031234567",
    ("07911 123456", "+44"): "+447911123456",
    ("0712345678", "+225"): "+2250712345678",
    ("(415) 555-0132", "+1"): "+14155550132",
    ("123", "+234"): None,
    ("", "+234"): None,
    ("abc", "+234"): None,
}
for (raw, dial), want in cases.items():
    got = normalize_phone(raw, dial)
    assert got == want, (raw, dial, got, want)
print("phone normaliser: all", len(cases), "cases pass")

engine = create_async_engine("sqlite+aiosqlite:///:memory:")
Session = async_sessionmaker(engine, expire_on_commit=False)

async def setup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from app.models.user import User
    async with Session() as s:
        s.add_all([User(id=1, email="a@x.com", full_name="A"), User(id=2, email="b@x.com", full_name="B")])
        await s.commit()
    async with Session() as s:
        s.add(Customer(user_id=1, name="Existing Ada", phone_number="+2340803 111 2222".replace(" ", ""), customer_type="active", has_purchased=True))
        s.add(Customer(user_id=1, name="Email Only", email="bola@example.com"))
        s.add(Customer(user_id=2, name="Other owner", phone_number="+2348039998888"))
        await s.commit()
asyncio.run(setup())

async def override_db():
    async with Session() as s:
        yield s

app = FastAPI()
app.include_router(customer_routes.router)
app.dependency_overrides[get_db] = override_db
app.dependency_overrides[get_current_user_id] = lambda: 1
client = TestClient(app)

# --- quick import rows ---
r = client.post("/api/customers/import-rows", json={"default_dial_code": "+234", "rows": [
    {"name": "Ada Contact", "phone_number": "0803 111 2222"},          # matches Existing Ada (stored with bad trunk 0)
    {"name": "Chidi", "phone_number": "0805 333 4444"},                # new
    {"name": "Chidi again", "phone_number": "+234 805 333 4444"},      # dup within batch
    {"name": "Bola", "phone_number": "0807 555 6666", "email": "BOLA@example.com"},  # email match, fills phone
    {"name": "", "phone_number": "0809 000 0000"},                     # no name
    {"name": "Bad number", "phone_number": "12"},                      # invalid phone
    {"name": "No contact"},                                            # no phone/email
    {"name": "Other owner dup", "phone_number": "0803 999 8888"},      # belongs to user 2 -> new for user 1
]})
print(r.status_code, r.json())
d = r.json()
assert r.status_code == 200
assert (d["created"], d["updated"], d["skipped"], len(d["failed"])) == (2, 1, 2, 3), d

async def check():
    async with Session() as s:
        rows = (await s.execute(select(Customer).where(Customer.user_id == 1).order_by(Customer.id))).scalars().all()
        return [(c.name, c.phone_number, c.email, c.customer_type, c.has_purchased) for c in rows]
state = asyncio.run(check())
for row in state: print("  ", row)
assert state[0] == ("Existing Ada", "+23408031112222", None, "active", True), "quick import must not rename/recategorise"
assert state[1][1] == "+2348075556666", "email match should get phone filled"

# re-import is idempotent
r2 = client.post("/api/customers/import-rows", json={"rows": [{"name": "Chidi", "phone_number": "08053334444"}]})
assert r2.json()["created"] == 0 and r2.json()["skipped"] == 1, r2.json()
print("re-import idempotent: ok")

# --- Excel import: only Name column required, partial success ---
df = pd.DataFrame({"Name": ["Dayo", "", "Efe", "Chidi Updated"], "Phone Number": [8061234567, "0806 000 0001", "bad", "0805 333 4444"],
                   "Customer Type": [None, None, None, "active"]})
buf = io.BytesIO(); df.to_excel(buf, index=False); buf.seek(0)
r3 = client.post("/api/customers/bulk-import", files={"file": ("c.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
print(r3.status_code, r3.json())
d3 = r3.json()
assert (d3["created"], d3["updated"], len(d3["failed"])) == (1, 1, 2), d3
assert d3["failed"][0]["row"] == "Row 3" and d3["failed"][1]["row"] == "Row 4", d3["failed"]
state = asyncio.run(check())
assert ("Dayo", "+2348061234567") in [(n, p) for n, p, *_ in state]
chidi = [x for x in state if x[1] == "+2348053334444"][0]
assert chidi[0] == "Chidi Updated" and chidi[3] == "active", chidi
print("excel import: ok")

# missing Name column
buf = io.BytesIO(); pd.DataFrame({"Phone": ["1"]}).to_excel(buf, index=False); buf.seek(0)
r4 = client.post("/api/customers/bulk-import", files={"file": ("c.xlsx", buf, "application/octet-stream")})
assert r4.status_code == 400, r4.text
# empty rows list rejected by schema
assert client.post("/api/customers/import-rows", json={"rows": []}).status_code == 422
print("ALL TESTS PASSED")
