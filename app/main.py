from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, Response, JSONResponse
from dotenv import load_dotenv
import logging
from pydantic import ValidationError

load_dotenv()
from app.database import get_db, AsyncSessionLocal
from app.models import Customer, User
from app.routes import auth, user, customer, message, notification, sale
from fastapi import APIRouter
from app.schemas.user import ResetPasswordRequest, SetInitialPasswordRequest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.controllers.auth_controller import AuthController
from app.services.email_service import email_service

from app.rate_limit import limiter, RateLimitedRouter, add_rate_limit_exception_handler

logging.basicConfig(level=logging.DEBUG)

import os

print("=== ENV CHECK ===")
print("GMAIL_USER:", os.getenv("GMAIL_USER"))
print("ALL KEYS:", list(os.environ.keys())[:20])

app = FastAPI(
    redirect_slashes=False,
    title="Careloop API",
    description="Customer Relationship Management API for Small Businesses",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)
@app.get("/health")
async def health():
    return {"status": "ok"}

# Add validation error middleware
@app.middleware("http")
async def catch_validation_errors(request: Request, call_next):
    try:
        response = await call_next(request)
        return response
    except ValidationError as e:
        return JSONResponse(
            status_code=422,
            content={"detail": e.errors()}
        )
@app.get("/")
async def root():
    try:
        with open("Frontend/careloop-landing.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return {"status": "ok", "message": "Careloop backend is live"}
    
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.database import engine
from app.models.base import Base

@app.on_event("startup")
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

app.state.limiter = limiter
add_rate_limit_exception_handler(app)

@app.get("/api/auth/verify-email")
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    result = await AuthController.verify_email(db, token)
    return result

main_router = APIRouter()

@main_router.get("/", response_class=HTMLResponse)
async def serve_landing():
    try:
        with open("Frontend/careloop-landing.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Landing page not found</h1>", status_code=404)

@main_router.get("/signup", response_class=HTMLResponse)
async def serve_signup():
    try:
        with open("Frontend/careloop-signup.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Signup page not found</h1>", status_code=404)

@main_router.get("/login", response_class=HTMLResponse)
async def serve_login_page():
    try:
        with open("Frontend/careloop-login.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Login page not found</h1>", status_code=404)

@main_router.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard_page():
    try:
        with open("Frontend/careloop-dashboard.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Dashboard page not found</h1>", status_code=404)

@main_router.get("/reports", response_class=HTMLResponse)
async def serve_reports_page():
    try:
        with open("Frontend/careloop-reports.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Reports page not found</h1>", status_code=404)

@main_router.get("/forgot-password", response_class=HTMLResponse)
async def serve_forgot_password_page():
    try:
        with open("Frontend/forgot-password.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Forgot password page not found</h1>", status_code=404)

@main_router.get("/reset-password", response_class=HTMLResponse)
async def serve_reset_password_page():
    try:
        with open("Frontend/reset-password.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Reset password page not found</h1>", status_code=404)

@main_router.get("/create-password", response_class=HTMLResponse)
async def serve_create_password_page():
    try:
        with open("Frontend/create-password.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Create password page not found</h1>", status_code=404)

app.mount("/assets", StaticFiles(directory="Frontend/assets"), name="assets")
app.include_router(main_router)
app.include_router(auth.router, prefix="/api")

@app.get("/verify-email")
async def verify_email_redirect(token: str, db: AsyncSession = Depends(get_db)):
    from app.controllers.auth_controller import AuthController
    await AuthController.verify_email(db, token)
    try:
        with open("Frontend/email-verified.html", "r", encoding="utf-8") as f:
            html = f.read()
            html = html.replace('href="/create-password"', f'href="/create-password?token={token}"')
            return HTMLResponse(content=html)
    except FileNotFoundError:
        return HTMLResponse(content=f"<h1>Email verified successfully!</h1><p><a href='/create-password?token={token}'>Create Password</a></p>", status_code=200)

@app.get("/favicon.ico")
async def favicon():
    try:
        with open("Frontend/favicon.ico", "rb") as f:
            return Response(content=f.read(), media_type="image/x-icon")
    except FileNotFoundError:
        return Response(content=b"", media_type="image/x-icon")

@app.get("/reset-password")
async def reset_password_page():
    try:
        with open("Frontend/reset-password.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Reset password page not found</h1>", status_code=404)

@app.post("/reset-password")
async def reset_password_submit(request: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    from app.schemas.user import ResetPasswordRequest
    from app.controllers.auth_controller import AuthController
    return await AuthController.reset_password(db, request)

@app.get("/signup-success", response_class=HTMLResponse)
async def serve_signup_success_page():
    try:
        with open("Frontend/signup-success.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Page not found</h1>", status_code=404)

@app.post("/set-initial-password")
async def set_initial_password_submit(request: SetInitialPasswordRequest, db: AsyncSession = Depends(get_db)):
    from app.schemas.user import SetInitialPasswordRequest
    from app.controllers.auth_controller import AuthController
    return await AuthController.set_initial_password(db, request.token, request.password)

app.include_router(user.router, prefix="/api")
app.include_router(customer.router)
app.include_router(message.router)
app.include_router(notification.router)
app.include_router(sale.router)

@app.get("/careloop-signup.html", response_class=HTMLResponse)
async def serve_signup_page():
    try:
        with open("Frontend/careloop-signup.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Page not found</h1>", status_code=404)

@app.get("/careloop-login.html", response_class=HTMLResponse)
async def serve_login_page():
    try:
        with open("Frontend/careloop-login.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Page not found</h1>", status_code=404)

@app.get("/careloop-dashboard.html", response_class=HTMLResponse)
async def serve_dashboard_page():
    try:
        with open("Frontend/careloop-dashboard.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Page not found</h1>", status_code=404)

@app.get("/forgot-password.html", response_class=HTMLResponse)
async def serve_forgot_password_page():
    try:
        with open("Frontend/forgot-password.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Page not found</h1>", status_code=404)

@app.get("/reset-password.html", response_class=HTMLResponse)
async def serve_reset_password_page():
    try:
        with open("Frontend/reset-password.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Page not found</h1>", status_code=404)

@app.get("/create-password.html", response_class=HTMLResponse)
async def serve_create_password_page():
    try:
        with open("Frontend/create-password.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Page not found</h1>", status_code=404)

@app.get("/customers.html", response_class=HTMLResponse)
async def serve_customers_page():
    try:
        with open("Frontend/customers.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Page not found</h1>", status_code=404)

@app.get("/api")
async def api_root():
    return {
        "message": "Careloop API is running",
        "version": "1.0.0",
        "docs": "/docs"
    }

@app.get("/test-db")
async def test_database():
    try:
        async for db in get_db():
            result = await db.execute(select(Customer).limit(1))
            customers = result.scalars().all()
            result = await db.execute(select(User).limit(1))
            users = result.scalars().all()
            return {
                "status": "connected", 
                "customer_count": len(customers),
                "user_count": len(users),
                "message": "Database connection successful!"
            }
    except Exception as e:
        return {
            "status": "error", 
            "error": str(e),
            "message": "Database connection failed"
        }


    except Exception as e:
        return {"error": str(e)}

@app.get("/users")
async def get_users():
    try:
        async for db in get_db():
            result = await db.execute(select(User))
            users = result.scalars().all()
            return {"users": [{"id": u.id, "email": u.email, "is_verified": u.is_email_verified} for u in users]}
    except Exception as e:
        return {"error": str(e)}
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

import asyncio
from datetime import datetime, timedelta
from sqlalchemy import delete

async def cleanup_unverified_users():
    while True:
        try:
            async with AsyncSessionLocal() as db:
                cutoff = datetime.utcnow() - timedelta(hours=24)
                await db.execute(
                    delete(User).where(
                        User.is_email_verified == False,
                        User.hashed_password == None,
                        User.created_at < cutoff
                    )
                )
                await db.commit()
                print(f"Cleanup: removed unverified accounts older than 24hrs")
        except Exception as e:
            print(f"Cleanup error: {e}")
        await asyncio.sleep(3600)

@app.on_event("startup")
async def start_cleanup():
    asyncio.create_task(cleanup_unverified_users())

@app.get("/debug-env")
async def debug_env():
    import os
    return {
        "gmail_user": os.getenv("GMAIL_USER"),
        "gmail_password_set": bool(os.getenv("GMAIL_APP_PASSWORD")),
        "mail_from_name": os.getenv("MAIL_FROM_NAME")
    }

async def check_birthdays():
    while True:
        try:
            today = datetime.utcnow()
            async with AsyncSessionLocal() as db:
                from sqlalchemy import select, and_, extract
                from app.models.customer import Customer
                from app.models.user import User
                result = await db.execute(
                    select(Customer).where(
                        and_(
                            extract('month', Customer.date_of_birth) == today.month,
                            extract('day', Customer.date_of_birth) == today.day,
                            Customer.date_of_birth != None
                        )
                    )
                )
                customers = result.scalars().all()
                for customer in customers:
                    owner = await db.get(User, customer.user_id)
                    if owner and owner.email:
                        await email_service.send_birthday_reminder(
                            owner.email,
                            customer.name,
                            owner.full_name or "there"
                        )
                        print(f"Birthday reminder sent for {customer.name} to {owner.email}")
        except Exception as e:
            print(f"Birthday check error: {e}")
        await asyncio.sleep(86400)

@app.on_event("startup")
async def start_birthday_checker():
    asyncio.create_task(check_birthdays())
