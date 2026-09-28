import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def create_tables():
    try:
        db_url = os.getenv("DATABASE_URL").replace("postgresql+asyncpg://", "postgresql://")
        db_url = db_url.replace("?sslmode=require", "").replace("&sslmode=require", "")

        conn = await asyncpg.connect(db_url, ssl="require")
        
        print("Connected to database successfully!")
        
        # Create customers table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                phone_number VARCHAR(50),
                email VARCHAR(255),
                date_of_birth DATE,
                customer_type VARCHAR(20) DEFAULT 'new',
                has_purchased BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        
        # Create purchases table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id SERIAL PRIMARY KEY,
                customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
                purchase_date DATE NOT NULL,
                notes TEXT,
                amount DECIMAL(10,2),
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        
        # Create follow_ups table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS follow_ups (
                id SERIAL PRIMARY KEY,
                customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
                scheduled_date DATE NOT NULL,
                follow_up_type VARCHAR(20) DEFAULT 'automatic',
                status VARCHAR(20) DEFAULT 'pending',
                message_suggestion TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        
        # Create customer_settings table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS customer_settings (
                id SERIAL PRIMARY KEY,
                customer_id INTEGER UNIQUE REFERENCES customers(id) ON DELETE CASCADE,
                repeat_followups_enabled BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        
        # Create users table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                username VARCHAR(100) UNIQUE,
                hashed_password VARCHAR(255) NOT NULL,
                full_name VARCHAR(255),
                is_email_verified BOOLEAN DEFAULT FALSE,
                email_verification_token VARCHAR(255),
                email_verification_expires_at TIMESTAMP,
                password_reset_token VARCHAR(255),
                password_reset_expires_at TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE,
                is_superuser BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                last_login_at TIMESTAMP
            );
        """)
        
        # Create indexes
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_users_email ON users(email);")
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_users_username ON users(username);")
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_users_email_verification_token ON users(email_verification_token);")
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_users_password_reset_token ON users(password_reset_token);")
        
        # Create alembic version table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL
            );
        """)
        
        await conn.execute("SELECT version_num FROM alembic_version")
        version_exists = await conn.fetchval("SELECT COUNT(*) FROM alembic_version")
        if version_exists == 0:
            await conn.execute("INSERT INTO alembic_version (version_num) VALUES ('002')")
        
        print("All tables created successfully!")
        
        # Check tables
        tables = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        print("Tables in database:")
        for row in tables:
            print(f"  - {row['tablename']}")
        
        await conn.close()
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(create_tables())
