from email.message import EmailMessage
import smtplib
import httpx
import base64
import time
import hashlib
import hmac
import json
import re
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os

load_dotenv()
from mirror_logic import get_merchant_transactions, calculate_merchant_settlement, save_transaction, send_alert_email
from web3_service import web3_service
import sqlite3
import uuid
import bcrypt
from pydantic import BaseModel
from typing import Optional
from contextlib import asynccontextmanager
from telegram_bot import start_telegram_bot, stop_telegram_bot, notify_channel

MONNIFY_API_KEY = os.getenv("MONNIFY_API_KEY", "")
MONNIFY_SECRET_KEY = os.getenv("MONNIFY_SECRET_KEY", "")
MONNIFY_CONTRACT_CODE = os.getenv("MONNIFY_CONTRACT_CODE", "")
MONNIFY_BASE_URL = os.getenv("MONNIFY_BASE_URL", "https://sandbox.monnify.com")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # await start_telegram_bot()
    yield
    # await stop_telegram_bot()

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
            merchant_credits INTEGER DEFAULT 0,
            alerts_remaining INTEGER DEFAULT 0,
            loyalty_points INTEGER DEFAULT 0,
            coin_balance REAL DEFAULT 0.0,
            web3_wallet_address TEXT DEFAULT NULL
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reference TEXT UNIQUE NOT NULL,
            merchant_id TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT DEFAULT 'PENDING',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cursor.execute("ALTER TABLE transactions ADD COLUMN merchant_credits INTEGER DEFAULT 0")
        print("Database updated with credits column!")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE transactions ADD COLUMN reference_number TEXT")
        cursor.execute("ALTER TABLE transactions ADD COLUMN is_suspicious BOOLEAN DEFAULT 0")
        cursor.execute("ALTER TABLE transactions ADD COLUMN suspicious_reason TEXT")
        print("Database updated with fake alert columns!")
    except:
        pass

    # Web3 & Coin Engine Schema Expansion
    try:
        cursor.execute("ALTER TABLE merchants ADD COLUMN loyalty_points INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE merchants ADD COLUMN coin_balance REAL DEFAULT 0.0")
        cursor.execute("ALTER TABLE merchants ADD COLUMN alerts_remaining INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE merchants ADD COLUMN web3_wallet_address TEXT DEFAULT NULL")
    except:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS coin_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_id TEXT NOT NULL,
            type TEXT NOT NULL,
            points_change INTEGER DEFAULT 0,
            coins_change REAL DEFAULT 0.0,
            alerts_change INTEGER DEFAULT 0,
            tx_hash TEXT DEFAULT NULL,
            status TEXT DEFAULT 'COMPLETED',
            details TEXT DEFAULT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

init_db()

def credit_loyalty_points(cursor, merchant_id: str, points: int = 10, details: str = "Verified alert processed"):
    """Helper to credit loyalty points for verified alerts and record audit log"""
    try:
        cursor.execute("UPDATE merchants SET loyalty_points = COALESCE(loyalty_points, 0) + ? WHERE merchant_id = ?", (points, merchant_id))
        cursor.execute("""
            INSERT INTO coin_transactions (merchant_id, type, points_change, status, details)
            VALUES (?, 'POINTS_EARNED', ?, 'COMPLETED', ?)
        """, (merchant_id, points, details))
    except Exception as e:
        print(f"Failed to credit loyalty points: {e}")

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
            INSERT INTO merchants (email, password_hash, bank_account, bank_name, merchant_id, merchant_credits, alerts_remaining, loyalty_points, coin_balance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (request.email, hashed_pw, request.bank_account, request.bank_name, merchant_id, 20, 20, 0, 0.0)) # 20 free credits
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

class NotificationPayload(BaseModel):
    raw_text: str
    amount: float
    merchant_id: str
    reference_number: Optional[str] = None
    is_suspicious: bool = False
    suspicious_reason: Optional[str] = None

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

@app.get("/api")
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
    cursor.execute("SELECT merchant_credits, alerts_remaining, loyalty_points, coin_balance, web3_wallet_address FROM merchants WHERE merchant_id = ?", (merchant_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        alerts = row[1] if row[1] is not None else row[0]
        return {
            "merchant": merchant_id, 
            "alerts_remaining": alerts,
            "loyalty_points": row[2] or 0,
            "coin_balance": row[3] or 0.0,
            "web3_wallet_address": row[4] or ""
        }
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
    cursor.execute("""
        SELECT merchant_credits, alerts_remaining, loyalty_points, coin_balance, web3_wallet_address 
        FROM merchants WHERE merchant_id = ?
    """, (m_id,))
    result = cursor.fetchone()
    if result:
        credits = result[0]
        alerts = result[1] if result[1] is not None else credits
        points = result[2] or 0
        coins = result[3] or 0.0
        wallet = result[4] or ""

        cursor.execute("""
            SELECT type, points_change, coins_change, alerts_change, status, details, created_at 
            FROM coin_transactions WHERE merchant_id = ? ORDER BY id DESC LIMIT 5
        """, (m_id,))
        tx_rows = cursor.fetchall()
        tx_history = [
            {
                "type": t[0],
                "points_change": t[1],
                "coins_change": t[2],
                "alerts_change": t[3],
                "status": t[4],
                "details": t[5],
                "created_at": t[6]
            }
            for t in tx_rows
        ]
        conn.close()
        return {
            "merchant_id": m_id, 
            "credits": credits,
            "alerts_remaining": alerts,
            "loyalty_points": points,
            "coin_balance": coins,
            "web3_wallet_address": wallet,
            "coin_history": tx_history
        }
    conn.close()
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

@app.post("/api/webhook/email")
async def email_webhook(request: Request, background_tasks: BackgroundTasks):
    # Extract fields from the request
    # Webhook parsers can send as JSON or Form parameters
    form_data = await request.form()
    
    to_field = form_data.get("to", "") or form_data.get("recipient", "")
    text_body = form_data.get("text", "") or form_data.get("body-plain", "")
    from_field = form_data.get("from", "") or form_data.get("sender", "")
    subject = form_data.get("subject", "")
    
    # Fallback to JSON if it's not a multipart form
    if not to_field and not text_body:
        try:
            json_data = await request.json()
            to_field = json_data.get("to", "") or json_data.get("recipient", "")
            text_body = json_data.get("text", "") or json_data.get("body", "")
            from_field = json_data.get("from", "") or json_data.get("sender", "")
            subject = json_data.get("subject", "")
        except:
            pass
            
    if not to_field or not text_body:
        raise HTTPException(status_code=400, detail="Missing required email fields: 'to' or 'text'")
        
    # Find merchant_id from the 'to' address (e.g. inflow-B1D71377@bankmirror.com.ng)
    match = re.search(r'inflow-([A-Z0-9]{8})@', to_field, re.IGNORECASE)
    if not match:
        return {"status": "Ignored", "reason": "No merchant ID in recipient address"}
        
    merchant_id = match.group(1).upper()
    
    # Verify merchant exists in database
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM merchants WHERE merchant_id = ?", (merchant_id,))
    merchant_row = cursor.fetchone()
    if not merchant_row:
        conn.close()
        return {"status": "Failed", "reason": f"Merchant ID {merchant_id} not found in database"}
        
    # Extract Amount (NGN / ₦ / N / Naira followed by digits)
    amount_match = re.search(r"(?:NGN|₦|N|NG|Naira)\s?([\d,]+(?:\.\d+)?)", text_body, re.IGNORECASE)
    if not amount_match:
        conn.close()
        return {"status": "Ignored", "reason": "No transaction amount found in email body"}
        
    amount_str = amount_match.group(1).replace(',', '')
    try:
        amount = float(amount_str)
    except:
        conn.close()
        return {"status": "Failed", "reason": "Failed to parse amount as float"}
        
    # Extract Reference Number
    ref_match = re.search(r'(?i)\b(?:ref|reference|txn id|trx|transaction|session id|receipt no|order no)\b[\s\:\-]*([A-Za-z0-9]{6,25})', text_body)
    reference_number = ref_match.group(1) if ref_match else None
    
    # Run validation checks (heuristics & duplicates)
    is_suspicious = False
    suspicious_reason = ""
    
    # 1. Zero instead of O in bank names check
    if re.search(r'(?i)(gtb|zenith|uba|firstbank|access|polaris|fcmb|stanbic)[^a-z\s]*0', text_body) or re.search(r'[a-zA-Z]0[a-zA-Z]', text_body):
        is_suspicious = True
        suspicious_reason += "[Heuristic] Suspicious characters (0 instead of O) detected. "
        
    # 2. Urgency key terms check
    urgency_keywords = [r'urgent', r'final warning', r'call this number', r'immediate action']
    for keyword in urgency_keywords:
        if re.search(keyword, text_body, re.IGNORECASE):
            is_suspicious = True
            suspicious_reason += f"[Heuristic] Urgency keyword detected: {keyword}. "
            
    # 3. Missing reference number
    if not reference_number:
        is_suspicious = True
        suspicious_reason += "[Reference] Missing reference number. "
        
    # 4. Duplicate Reference Number check
    if reference_number:
        cursor.execute("SELECT id FROM transactions WHERE reference_number = ?", (reference_number,))
        if cursor.fetchone():
            is_suspicious = True
            suspicious_reason += "[Backend] Duplicate reference number. "
            
    # Save the transaction log
    cursor.execute("""
        INSERT INTO transactions (raw_text, amount, merchant_id, reference_number, is_suspicious, suspicious_reason)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (text_body, amount, merchant_id, reference_number, is_suspicious, suspicious_reason))
    
    # Deduct credit and award loyalty points if alert is valid (not suspicious)
    if not is_suspicious:
        cursor.execute("SELECT merchant_credits, alerts_remaining FROM merchants WHERE merchant_id = ?", (merchant_id,))
        result = cursor.fetchone()
        if result and (result[0] or 0) > 0:
            cursor.execute("""
                UPDATE merchants 
                SET merchant_credits = merchant_credits - 1,
                    alerts_remaining = MAX(0, COALESCE(alerts_remaining, 1) - 1)
                WHERE merchant_id = ?
            """, (merchant_id,))
            
            # Step 2: Trigger +10 points on verified incoming alert
            credit_loyalty_points(cursor, merchant_id, points=10, details=f"Verified email alert #{reference_number or 'N/A'}")

            # Send real-time notification
            await notify_channel(f"💰 [BANK MIRROR] New Alert via Webhook!\n\nAmount: ₦{amount:,.2f}\nMerchant: {merchant_id}\nRef: {reference_number}")
            background_tasks.add_task(trigger_desktop_notification, "Bank Mirror Alert", f"New Alert: ₦{amount:,.2f} received!")
            
    conn.commit()
    conn.close()
    
    return {
        "status": "Success",
        "message": "Notification parsed",
        "merchant_id": merchant_id,
        "amount": amount,
        "is_suspicious": is_suspicious,
        "suspicious_reason": suspicious_reason
    }

@app.post("/notifications")
async def receive_notification(payload: NotificationPayload, background_tasks: BackgroundTasks):
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    
    # 1. Check for Duplicate Reference
    if payload.reference_number:
        cursor.execute("SELECT id FROM transactions WHERE reference_number = ?", (payload.reference_number,))
        if cursor.fetchone():
            payload.is_suspicious = True
            payload.suspicious_reason = (payload.suspicious_reason or "") + " [Backend] Duplicate reference number. "
            
    # 2. Save Transaction
    cursor.execute("""
        INSERT INTO transactions (raw_text, amount, merchant_id, reference_number, is_suspicious, suspicious_reason)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (payload.raw_text, payload.amount, payload.merchant_id, payload.reference_number, payload.is_suspicious, payload.suspicious_reason))
    
    # 3. Deduct Credits and award loyalty points if Valid
    if not payload.is_suspicious:
        cursor.execute("SELECT merchant_credits, alerts_remaining FROM merchants WHERE merchant_id = ?", (payload.merchant_id,))
        result = cursor.fetchone()
        if result and (result[0] or 0) > 0:
            cursor.execute("""
                UPDATE merchants 
                SET merchant_credits = merchant_credits - 1,
                    alerts_remaining = MAX(0, COALESCE(alerts_remaining, 1) - 1)
                WHERE merchant_id = ?
            """, (payload.merchant_id,))
            
            # Step 2: Trigger +10 points on verified incoming alert
            credit_loyalty_points(cursor, payload.merchant_id, points=10, details=f"Verified bridge alert #{payload.reference_number or 'N/A'}")

            # Send real-time notification
            await notify_channel(f"💰 [BANK MIRROR] New Alert Received via Bridge!\n\nAmount: ₦{payload.amount:,.2f}\nMerchant: {payload.merchant_id}\nRef: {payload.reference_number}")
            background_tasks.add_task(trigger_desktop_notification, "Bank Mirror Alert", f"New Alert: ₦{payload.amount:,.2f} received!")
            
    conn.commit()
    conn.close()
    
    return {"status": "Success", "message": "Notification processed"}

async def get_auth_token():
    """Generates the Bearer Token required for Monnify API calls"""
    auth_str = f"{MONNIFY_API_KEY}:{MONNIFY_SECRET_KEY}"
    encoded_auth = base64.b64encode(auth_str.encode()).decode()

    headers = {"Authorization": f"Basic {encoded_auth}"}
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{MONNIFY_BASE_URL}/api/v1/auth/login", headers=headers)
        if response.status_code == 200:
            return response.json()['responseBody']['accessToken']
        raise HTTPException(status_code=401, detail="Failed to authenticate with Monnify")

@app.post("/subscribe/initiate")
async def initiate_subscription(amount: int, email: str, name: str, merchant_id: str):
    # Enforce your 1,000 to 20,000 range
    if not (1000 <= amount <= 20000):
        raise HTTPException(status_code=400, detail="Amount must be between 1,000 and 20,000 NGN")

    token = await get_auth_token()
    ref = f"BM-{int(time.time())}"

    # Log payment intent in database
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO payments (reference, merchant_id, amount) VALUES (?, ?, ?)", (ref, merchant_id, amount))
    conn.commit()
    conn.close()

    payload = {
        "amount": amount,
        "customerName": name,
        "customerEmail": email,
        "paymentReference": ref,
        "paymentDescription": f"BankMirror Subscription - {amount}",
        "currencyCode": "NGN",
        "contractCode": MONNIFY_CONTRACT_CODE,
        "redirectUrl": "http://127.0.0.1:8000/success", # In production, this should be frontend URL
        "paymentMethods": ["CARD", "ACCOUNT_TRANSFER"]
    }

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        res = await client.post(f"{MONNIFY_BASE_URL}/api/v1/merchant/transactions/init-transaction", json=payload, headers=headers)
        return res.json()

@app.post("/monnify-webhook")
async def monnify_webhook(request: Request):
    payload_bytes = await request.body()
    monnify_signature = request.headers.get("monnify-signature")
    
    if not monnify_signature:
        raise HTTPException(status_code=400, detail="Missing signature")
        
    # Verify signature
    computed_hash = hmac.new(
        MONNIFY_SECRET_KEY.encode('utf-8'),
        payload_bytes,
        hashlib.sha512
    ).hexdigest()
    
    if computed_hash != monnify_signature:
        raise HTTPException(status_code=401, detail="Invalid signature")
        
    data = await request.json()
    event_type = data.get("eventType")
    event_data = data.get("eventData", {})
    
    if event_type == "SUCCESSFUL_TRANSACTION":
        ref = event_data.get("paymentReference")
        amount_paid = event_data.get("amountPaid")
        
        if ref and amount_paid:
            conn = sqlite3.connect('bank_mirror.db')
            cursor = conn.cursor()
            
            cursor.execute("SELECT merchant_id, status FROM payments WHERE reference = ?", (ref,))
            row = cursor.fetchone()
            
            if row and row[1] == 'PENDING':
                merchant_id = row[0]
                
                # 1,000 NGN = 100 alerts -> 10 NGN per alert
                credits_to_add = int(float(amount_paid) / 10)
                
                cursor.execute("UPDATE payments SET status = 'PAID' WHERE reference = ?", (ref,))
                cursor.execute("""
                    UPDATE merchants 
                    SET merchant_credits = merchant_credits + ?, 
                        alerts_remaining = COALESCE(alerts_remaining, 0) + ? 
                    WHERE merchant_id = ?
                """, (credits_to_add, credits_to_add, merchant_id))
                conn.commit()
            
            conn.close()
            
    return {"status": "ok"}

# ==========================================
# WEB3 & $BMIRROR COIN ENGINE ENDPOINTS
# ==========================================

class ConvertPointsRequest(BaseModel):
    merchant_id: str
    points: Optional[int] = 1000

class BuyAlertsWithCoinRequest(BaseModel):
    merchant_id: str
    package_id: Optional[str] = "tier_100"
    alerts_count: Optional[int] = 100
    coin_cost: Optional[float] = 10.0

class LinkWalletRequest(BaseModel):
    merchant_id: str
    wallet_address: str

class WithdrawCoinsRequest(BaseModel):
    merchant_id: str
    wallet_address: str
    amount: float

@app.post("/api/convert-points")
async def convert_points(request: ConvertPointsRequest):
    """
    Step 2: Points-to-Coin Swap Endpoint
    Converts accumulated verified alert points into $BMIRROR coins (Threshold: 1,000 points = 10 $BMIRROR)
    """
    pts = request.points if (request.points and request.points > 0) else 1000
    if pts < 1000:
        raise HTTPException(status_code=400, detail="Minimum conversion threshold is 1,000 points.")
    
    convert_chunks = pts // 1000
    points_to_deduct = convert_chunks * 1000
    coins_to_credit = convert_chunks * 10.0

    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    cursor.execute("SELECT loyalty_points, coin_balance FROM merchants WHERE merchant_id = ?", (request.merchant_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Merchant not found.")
    
    current_points = row[0] or 0
    current_coins = row[1] or 0.0

    if current_points < points_to_deduct:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Insufficient points. You have {current_points:,} pts, but {points_to_deduct:,} pts are required to swap.")

    new_points = current_points - points_to_deduct
    new_coins = round(current_coins + coins_to_credit, 2)

    cursor.execute("UPDATE merchants SET loyalty_points = ?, coin_balance = ? WHERE merchant_id = ?",
                   (new_points, new_coins, request.merchant_id))
    
    cursor.execute("""
        INSERT INTO coin_transactions (merchant_id, type, points_change, coins_change, status, details)
        VALUES (?, 'POINTS_CONVERTED', ?, ?, 'COMPLETED', ?)
    """, (request.merchant_id, -points_to_deduct, coins_to_credit, f"Converted {points_to_deduct:,} points into +{coins_to_credit} $BMIRROR coins"))

    conn.commit()
    conn.close()

    return {
        "status": "Success",
        "message": f"Successfully converted {points_to_deduct:,} points to {coins_to_credit} $BMIRROR coins!",
        "loyalty_points": new_points,
        "coin_balance": new_coins,
        "coins_earned": coins_to_credit
    }

@app.post("/api/buy-alerts-with-coin")
async def buy_alerts_with_coin(request: BuyAlertsWithCoinRequest):
    """
    Step 3: In-App Coin Purchase Logic
    Deducts coins from coin_balance and credits alerts_remaining quota (10 Coins = 100 Alerts)
    """
    alerts = request.alerts_count or 100
    cost = request.coin_cost or 10.0

    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    cursor.execute("SELECT coin_balance, merchant_credits, alerts_remaining FROM merchants WHERE merchant_id = ?", (request.merchant_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Merchant not found.")

    current_coins = row[0] or 0.0
    current_credits = row[1] or 0
    current_alerts = row[2] if row[2] is not None else current_credits

    if current_coins < cost:
        conn.close()
        raise HTTPException(
            status_code=400, 
            detail=f"Insufficient $BMIRROR balance. You have {current_coins:,.2f} coins, but {cost} coins are required for this package."
        )

    new_coins = round(current_coins - cost, 2)
    new_credits = current_credits + alerts
    new_alerts = current_alerts + alerts

    cursor.execute("""
        UPDATE merchants 
        SET coin_balance = ?, merchant_credits = ?, alerts_remaining = ?
        WHERE merchant_id = ?
    """, (new_coins, new_credits, new_alerts, request.merchant_id))

    cursor.execute("""
        INSERT INTO coin_transactions (merchant_id, type, coins_change, alerts_change, status, details)
        VALUES (?, 'ALERTS_PURCHASED', ?, ?, 'COMPLETED', ?)
    """, (request.merchant_id, -cost, alerts, f"Redeemed {alerts} alerts for {cost} $BMIRROR coins ({request.package_id})"))

    conn.commit()
    conn.close()

    return {
        "status": "Success",
        "message": f"Successfully purchased {alerts} alerts with {cost} $BMIRROR coins!",
        "coin_balance": new_coins,
        "alerts_remaining": new_alerts,
        "alerts_added": alerts
    }

@app.post("/api/link-wallet")
async def link_wallet(request: LinkWalletRequest):
    """Links merchant's external Polygon/EVM Web3 wallet address"""
    if not web3_service.validate_wallet_address(request.wallet_address):
        raise HTTPException(status_code=400, detail="Invalid Polygon / EVM wallet address. Format must be 0x followed by 40 hex characters.")

    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM merchants WHERE merchant_id = ?", (request.merchant_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Merchant not found.")

    cursor.execute("UPDATE merchants SET web3_wallet_address = ? WHERE merchant_id = ?", (request.wallet_address, request.merchant_id))
    conn.commit()
    conn.close()
    return {"status": "Success", "message": "Web3 wallet linked successfully!", "wallet_address": request.wallet_address}

@app.post("/api/withdraw-coins-to-wallet")
async def withdraw_coins_to_wallet(request: WithdrawCoinsRequest):
    """
    Step 5: On-Chain Withdrawal (Web3 Minting / Transfer Endpoint)
    Dispatches real $BMIRROR tokens from treasury wallet to external merchant wallet
    """
    if request.amount <= 0:
        raise HTTPException(status_code=400, detail="Withdrawal amount must be greater than zero.")

    if not web3_service.validate_wallet_address(request.wallet_address):
        raise HTTPException(status_code=400, detail="Invalid Polygon wallet address format.")

    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    cursor.execute("SELECT coin_balance FROM merchants WHERE merchant_id = ?", (request.merchant_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Merchant not found.")

    current_balance = row[0] or 0.0
    if current_balance < request.amount:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Insufficient coins. Available: {current_balance} $BMIRROR, Requested: {request.amount}")

    tx_result = web3_service.withdraw_to_wallet(request.wallet_address, request.amount)
    if not tx_result.get("success"):
        conn.close()
        raise HTTPException(status_code=500, detail=tx_result.get("error", "Web3 transfer failed."))

    new_coins = round(current_balance - request.amount, 2)
    cursor.execute("UPDATE merchants SET coin_balance = ?, web3_wallet_address = ? WHERE merchant_id = ?",
                   (new_coins, request.wallet_address, request.merchant_id))

    cursor.execute("""
        INSERT INTO coin_transactions (merchant_id, type, coins_change, tx_hash, status, details)
        VALUES (?, 'WITHDRAWAL', ?, ?, ?, ?)
    """, (request.merchant_id, -request.amount, tx_result.get("tx_hash"), tx_result.get("status", "COMPLETED"),
          f"Withdrew {request.amount} $BMIRROR to {request.wallet_address} ({tx_result.get('mode')})"))

    conn.commit()
    conn.close()

    return {
        "status": "Success",
        "message": f"Successfully processed withdrawal of {request.amount} $BMIRROR coins!",
        "coin_balance": new_coins,
        "tx_details": tx_result
    }

# Serve the frontend web app from the "www" directory
app.mount("/", StaticFiles(directory="www", html=True), name="static")