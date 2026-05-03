from email.message import EmailMessage
import smtplib
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os

load_dotenv()
from mirror_logic import get_merchant_transactions, calculate_merchant_settlement, save_transaction, send_alert_email
import sqlite3
import uuid
import bcrypt
from pydantic import BaseModel
from contextlib import asynccontextmanager
from telegram_bot import start_telegram_bot, stop_telegram_bot, notify_channel

@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_telegram_bot()
    yield
    await stop_telegram_bot()

def trigger_desktop_notification(title, message):
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name='Bank Mirror',
            timeout=5
        )
    except Exception as e:
        print(f"Desktop notification failed: {e}")

# 1. First, create the app
app = FastAPI(title="Bank Mirror Dashboard API", lifespan=lifespan)

# 2. Then, add the middleware (No yellow line anymore!)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Setup the database
def init_db():
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            raw_text TEXT,
            amount REAL,
            merchant_id TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS merchants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            bank_account TEXT,
            bank_name TEXT,
            merchant_id TEXT UNIQUE NOT NULL,
            merchant_credits INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS workers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_id TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS worker_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id INTEGER NOT NULL,
            merchant_id TEXT NOT NULL,
            login_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_active DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cursor.execute("ALTER TABLE transactions ADD COLUMN merchant_credits INTEGER DEFAULT 0")
        print("Database updated with credits column!")
    except:
        pass
    conn.commit()
    conn.close()

init_db()

class RegisterRequest(BaseModel):
    email: str
    password: str
    bank_account: str
    bank_name: str

class LoginRequest(BaseModel):
    email: str
    password: str

@app.post("/register")
async def register_merchant(request: RegisterRequest):
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    
    # Check if email exists
    cursor.execute("SELECT email FROM merchants WHERE email = ?", (request.email,))
    if cursor.fetchone():
        conn.close()
        return {"error": "Email already registered"}
        
    merchant_id = str(uuid.uuid4())[:8].upper() # e.g. 5A2B9C10
    hashed_pw = bcrypt.hashpw(request.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    try:
        cursor.execute("""
            INSERT INTO merchants (email, password_hash, bank_account, bank_name, merchant_id, merchant_credits)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (request.email, hashed_pw, request.bank_account, request.bank_name, merchant_id, 20)) # 20 free credits
        conn.commit()
    except Exception as e:
        conn.close()
        return {"error": str(e)}
        
    conn.close()
    return {"message": "Registration successful", "merchant_id": merchant_id}

@app.post("/login")
async def login_merchant(request: LoginRequest):
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT password_hash, merchant_id, bank_account, bank_name FROM merchants WHERE email = ?", (request.email,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return {"error": "Invalid credentials"}
        
    password_hash, merchant_id, bank_account, bank_name = row
    
    if not bcrypt.checkpw(request.password.encode('utf-8'), password_hash.encode('utf-8')):
        return {"error": "Invalid credentials"}
        
    return {
        "message": "Login successful", 
        "merchant_id": merchant_id,
        "bank_account": bank_account,
        "bank_name": bank_name
    }

class WorkerCreateRequest(BaseModel):
    merchant_id: str
    username: str
    password: str

class WorkerLoginRequest(BaseModel):
    username: str
    password: str

@app.post("/worker/create")
async def create_worker(request: WorkerCreateRequest):
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    
    # Check if username exists globally
    cursor.execute("SELECT id FROM workers WHERE username = ?", (request.username,))
    if cursor.fetchone():
        conn.close()
        return {"error": "Username already taken"}
        
    hashed_pw = bcrypt.hashpw(request.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    try:
        cursor.execute("INSERT INTO workers (merchant_id, username, password_hash) VALUES (?, ?, ?)",
                       (request.merchant_id, request.username, hashed_pw))
        conn.commit()
    except Exception as e:
        conn.close()
        return {"error": str(e)}
        
    conn.close()
    return {"message": "Worker created successfully!"}

@app.post("/worker/login")
async def login_worker(request: WorkerLoginRequest):
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, merchant_id, password_hash FROM workers WHERE username = ?", (request.username,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return {"error": "Invalid credentials"}
        
    worker_id, merchant_id, password_hash = row
    
    if not bcrypt.checkpw(request.password.encode('utf-8'), password_hash.encode('utf-8')):
        conn.close()
        return {"error": "Invalid credentials"}
        
    # Log session
    cursor.execute("INSERT INTO worker_sessions (worker_id, merchant_id) VALUES (?, ?)", (worker_id, merchant_id))
    conn.commit()
    conn.close()
    
    return {"message": "Worker login successful", "merchant_id": merchant_id, "role": "worker"}

@app.get("/merchant/workers/{merchant_id}")
async def get_merchant_workers(merchant_id: str):
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(id) FROM workers WHERE merchant_id = ?", (merchant_id,))
    total_workers = cursor.fetchone()[0]
    
    # Active workers: sessions created in the last 24 hours (simplified for now)
    cursor.execute("SELECT COUNT(DISTINCT worker_id) FROM worker_sessions WHERE merchant_id = ? AND login_time >= datetime('now', '-1 day')", (merchant_id,))
    active_workers = cursor.fetchone()[0]
    
    conn.close()
    return {"total": total_workers, "active": active_workers}

@app.get("/")
def home():
    return {"status": "online", "message": "Bank Mirror API is Live"}


@app.get("/merchant/dashboard/{merchant_id}")
async def read_merchant_data(merchant_id: str):
    data = get_merchant_transactions(merchant_id)
    
    if not data:
        return {"message": f"No transactions found for merchant: {merchant_id}"}
        
    return {
        "merchant": merchant_id,
        "total_inflows": len(data),
        "history": data
    }

@app.get("/merchant/settlement/{merchant_id}")
async def get_settlement(merchant_id: str):
    settlement = calculate_merchant_settlement(merchant_id)
    
    if settlement["gross_volume"] == 0:
        return {"status": "No data", "message": "No transactions for this merchant today."}
        
    return {
        "merchant": merchant_id,
        "currency": "NGN",
        "breakdown": settlement,
        "status": "Ready for Settlement"
    }

# Update your POST endpoint to include credit logic
@app.post("/mirror")
async def mirror_transaction(amount: float, merchant_id: str, background_tasks: BackgroundTasks):
    # Call the logic function we updated in mirror_logic.py
    # It will now automatically deduct 1 credit or block the alert
    result = save_transaction("Manual Alert Entry", amount, merchant_id)
    if "Success" in result:
        await notify_channel(f"💰 [BANK MIRROR] New Alert!\n\nReceived: ₦{amount:,.2f}\nMerchant: {merchant_id}")
        background_tasks.add_task(trigger_desktop_notification, "Bank Mirror Alert", f"New Alert: ₦{amount:,.2f} received!")
    return result

# Add this new endpoint so merchants can check their balance
@app.get("/merchant/balance/{merchant_id}")
async def check_balance(merchant_id: str):
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    cursor.execute("SELECT merchant_credits FROM merchants WHERE merchant_id = ?", (merchant_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {"merchant": merchant_id, "alerts_remaining": row[0]}
    return {"error": "Merchant not found"}
@app.get("/merchant/instructions")
async def get_payment_info():
    return {
        "hub_name": "Zaria Bank Mirror Hub",
        "pricing_model": "1,000 NGN per 100 Credits (10 NGN/Alert)",
        "deposit_account": {
            "bank": "OPay / Moniepoint", # Update this with your specific bank name
            "account_number": "7035141339",
            "account_name": "Abdulkareem Muhammad Olayiwola"
        },
        "activation_process": "After payment, please send the receipt to the Hub Admin. Your 100 alerts will be activated immediately upon verification."
    }
@app.get("/api/dashboard-stats")
async def get_dashboard_stats():
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    
    # Get total alerts
    cursor.execute("SELECT COUNT(*) FROM transactions")
    count = cursor.fetchone()[0]
    
    # Get total revenue
    cursor.execute("SELECT SUM(amount) FROM transactions")
    rev = cursor.fetchone()[0] or 0
    
    conn.close()
    return {
        "total_alerts": count,
        "total_revenue": rev
    }
@app.get("/api/merchant/{m_id}")
async def get_merchant_details(m_id: str):
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    cursor.execute("SELECT merchant_credits FROM merchants WHERE merchant_id = ?", (m_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        # Email trigger safely removed here to prevent spam
        return {"merchant_id": m_id, "credits": result[0]}
    return {"error": "Merchant not found"}

@app.post("/transaction-alert")
async def trigger_transaction_alert(background_tasks: BackgroundTasks):
    user_email = "receiver@example.com"
    background_tasks.add_task(
        send_alert_email, 
        user_email, 
        "Bank Mirror Alert", 
        "A new transaction has been detected on your monitored account."
    )
    return {"status": "Success", "message": "Alert processing in background"}