import requests
import os
import smtplib
from email.message import EmailMessage
import time
import sqlite3
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.live import Live
from rich import box

console = Console()

def check_watch_alerts():
    try:
        with open("new_alert.txt", "r", encoding="utf-8") as f:
            content = f.read().strip()
            return content if content else None
    except FileNotFoundError:
        return None

def init_db():
    conn = sqlite3.connect('bank_mirror.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS transactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  raw_text TEXT,
                  amount REAL)''')
    conn.commit()
    conn.close()
def get_historical_stats():
    conn = sqlite3.connect('bank_mirror.db')
    c = conn.cursor()
    c.execute("SELECT SUM(amount), COUNT(id) FROM transactions")
    row = c.fetchone()
    conn.close()
    return (row[0] or 0.0, row[1] or 0)

def save_transaction(raw, amt, m_id):
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()

    # 1. Check if the merchant has alerts remaining
    cursor.execute("SELECT merchant_credits FROM merchants WHERE merchant_id = ?", (m_id,))
    result = cursor.fetchone()
    
    if result and result[0] > 0:
        # 2. Deduct 1 credit for the mirror service
        new_balance = result[0] - 1
        cursor.execute("UPDATE merchants SET merchant_credits = ? WHERE merchant_id = ?", (new_balance, m_id))
        
            # 3. Save the alert to the log
    cursor.execute("INSERT INTO transactions (raw_text, amount, merchant_id) VALUES (?, ?, ?)", (raw, amt, m_id))
            
            # --- NEW: TRIGGER REAL-TIME PING HERE ---
            # This sends the instant notification to the merchant's phone
    send_realtime_ping(m_id, amt) 
    # Connect to database and fetch credits
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT merchant_credits FROM merchants WHERE merchant_id = ?", (m_id,))
    result = cursor.fetchone()
    merchant_credits = result[0] if result else 0        

    conn.close()            

def save_transaction(raw, amt, m_id):
    # ADD THESE TWO LINES TO CLEAR THE YELLOW LINES
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()

    # 1. Fetch current credits first
    cursor.execute("SELECT merchant_credits FROM merchants WHERE merchant_id = ?", (m_id,))
    result = cursor.fetchone()
    merchant_credits = result[0] if result else 0

    # 2. Now check the condition
    if merchant_credits > 0:
        # Subtract one credit
        new_balance = merchant_credits - 1
        cursor.execute("UPDATE merchants SET merchant_credits = ? WHERE merchant_id = ?", (new_balance, m_id))
        
        # Log the transaction
        cursor.execute("INSERT INTO transactions (raw_text, amount, merchant_id) VALUES (?, ?, ?)", (raw, amt, m_id))
        conn.commit()
def display_dashboard(total_revenue, alert_count, last_amount):
    table = Table(show_header=True, header_style="bold magenta", box=box.ROUNDED)
    table.add_column("Metric", style="dim", width=20)
    table.add_column("Value", justify="right", style="bold green")

    table.add_row("Total Alerts", str(alert_count))
    table.add_row("Last Received", f"N{last_amount:,.2f}")
    table.add_row("Total Revenue", f"N{total_revenue:,.2f}")
    table.add_row("Current Billing", f"N{alert_count * 50:,.2f}", style="bold cyan")

    return Panel(
        table,
        title="[bold white]BANK MIRROR WORKER[/bold white]",
        subtitle="[yellow]Scanning... (Ctrl+C to Stop)[/yellow]",
        border_style="blue"
    )

def send_telegram_alert(amount, total):
    token = "7971237073:AAGfX_KMA7_y1BzLHQEw3i7URkmQHX9DxJw"
    chat_id = "5895654402"
    message = f"💰 [BANK MIRROR] New Alert!\n\nReceived: N{amount:,.2f}\nTotal Revenue: N{total:,.2f}"
    url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    try:
        response = requests.get(url)
        console.print(f"[bold green]Telegram Log: {response.status_code}[/bold green]")
    except Exception as e:
        console.print(f"[bold red]Notification Failed: {e}[/bold red]")

def start_worker():
    init_db()
    total_rev, alert_cnt = get_historical_stats()
    last_amt = 0.0

    with Live(display_dashboard(total_rev, alert_cnt, last_amt), refresh_per_second=1) as live:
        while True:
            try:
                with open("new_alert.txt", "r") as f:
                    alert_data = f.read().strip()
            except FileNotFoundError:
                alert_data = None

            if alert_data:
                try:
                    parts = alert_data.split(' ')
                    raw_amt_str = parts[2] if len(parts) > 2 else ''
                    clean_amt_str = ''.join(c for c in raw_amt_str if c.isdigit() or c == '.')

                    if clean_amt_str:
                        amt_value = float(clean_amt_str)
                        save_transaction(alert_data, amt_value)
                        total_rev += amt_value
                        alert_cnt += 1
                        last_amt = amt_value
                        send_telegram_alert(amt_value, total_rev)
                        live.update(display_dashboard(total_rev, alert_cnt, last_amt))

                    open("new_alert.txt", "w").close()
                except Exception as e:
                    console.print(f"[bold red]Processing Error: {e}[/bold red]")

            time.sleep(10)
def admin_portal():
    print("\n--- 🔐 BANK MIRROR ADMIN PORTAL ---")
    pin = input("Enter Admin PIN: ")
    if pin == "0000":
        print("\n--- 📊 BUSINESS REPORT ---")
        rev, count = get_historical_stats()
        print(f"Total Alerts Processed: {count}")
        print(f"Total Lifetime Revenue: ₦{rev:,.2f}")
        input("\nPress Enter to return to menu...")
    else:
        print("❌ Invalid PIN. Access Denied.")
def admin_portal():
    # 1. Define the correct PIN
    correct_pin = "1234"  # Change this to your actual secret PIN
    
    # 2. Ask the user for the PIN (This fixes the 'pin' error)
    pin = input("Enter Admin PIN: ")

    if pin == correct_pin:
        rev, count = get_historical_stats()
        print(f"Total Alerts Processed: {count}")
        print(f"Total Lifetime Revenue: #{rev:,.2f}")
        input("\nPress Enter to return to menu...")
    else:
        print("❌ Invalid PIN. Access Denied.")

# Move this function so it starts at the very left margin
def check_performance_rewards(m_id):
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    
    # ... rest of your reward logic ...
    cursor.execute("SELECT COUNT(id) FROM transactions WHERE merchant_id = ?", (m_id,))
    total_count = cursor.fetchone()[0]
    
    if total_count == 500:
        cursor.execute("UPDATE merchants SET merchant_credits = merchant_credits + 50 WHERE merchant_id = ?", (m_id,))
        conn.commit()
    
    conn.close()        

def get_merchant_transactions(m_id: str):
    conn = sqlite3.connect("bank_mirror.db")
    cursor = conn.cursor()
    query = "SELECT * FROM transactions WHERE merchant_id = ?"
    cursor.execute(query, (m_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

if __name__ == "__main__":
    init_db()
    while True:
        print("\n--- 🖥️ BANK MIRROR MAIN MENU ---")
        print("1. Start Worker Terminal (Scanning)")
        print("2. Enter Admin Portal (Reports)")
        print("3. Exit")

        choice = input("Select an option: ")

        if choice == "1":
            start_worker()
        elif choice == "2":
            admin_portal()
        elif choice == "3":
            break


def calculate_merchant_settlement(m_id: str):
    conn = sqlite3.connect("bank_mirror.db")
    cursor = conn.cursor()
    
    # Sum only the amounts for today for this specific merchant
    query = """
    SELECT SUM(amount) FROM transactions 
    WHERE merchant_id = ? 
    AND date(created_at) = date('now')
    """
    cursor.execute(query, (m_id,))
    total_inflow = cursor.fetchone()[0] or 0.0
    
    # Business Rules
    fee_percentage = 0.01  # 1% Zaria Hub Fee
    hub_revenue = total_inflow * fee_percentage
    merchant_net = total_inflow - hub_revenue
    
    conn.close()
    
    return {
        "gross_volume": total_inflow,
        "hub_fee": round(hub_revenue, 2),
        "net_payout": round(merchant_net, 2)
    }
import sqlite3

def calculate_merchant_settlement(m_id: str):
    conn = sqlite3.connect("bank_mirror.db")
    cursor = conn.cursor()
    
    # Calculate today's total for this specific merchant
    query = """
    SELECT SUM(amount) FROM transactions 
    WHERE merchant_id = ? 
    AND date(created_at) = date('now')
    """
    cursor.execute(query, (m_id,))
    total_inflow = cursor.fetchone()[0] or 0.0
    
    # Zaria Hub Business Logic: 1% Fee
    fee_percentage = 0.01 
    hub_revenue = total_inflow * fee_percentage
    merchant_net = total_inflow - hub_revenue
    
    conn.close()
    
    return {
        "gross_volume": total_inflow,
        "hub_fee": round(hub_revenue, 2),
        "net_payout": round(merchant_net, 2)
    }
def top_up_merchant(m_id: str, amount_paid: float):
    # Logic: 1,000 NGN = 100 Alerts (10 NGN per alert)
    credits_to_add = int(amount_paid / 10)
    
    conn = sqlite3.connect("bank_mirror.db")
    cursor = conn.cursor()
    
    # In a real app, you'd have a separate 'merchants' table, 
    # but for now, we'll track it based on their last transaction or a new settings table.
    print(f"Added {credits_to_add} alerts to {m_id}'s account.")
    
    conn.commit()
    conn.close()
    return credits_to_add
import requests

def send_realtime_ping(m_id, amount):
    # This is a placeholder for your chosen notification service (e.g., Telegram Bot)
    # 1,000 NGN per 100 Credits model ensures this is only sent for paid users
    
    message = f"🔔 Zaria Hub Alert: You received ₦{amount:,.2f}! Remaining alerts: (Check Dashboard)"
    
    # Example using a Telegram Bot API
    bot_token = "YOUR_BOT_TOKEN"
    chat_id = "MERCHANT_CHAT_ID" 
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={chat_id}&text={message}"
    
    try:
        requests.get(url)
        print(f"🚀 Ping sent to {m_id}")
    except Exception as e:
        print(f"❌ Ping failed: {e}")


def send_alert_email(recipient_email, subject, body):
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    
    sender_email = os.getenv("EMAIL_ADDRESS")
    sender_password = os.getenv("EMAIL_PASSWORD")
    
    msg['From'] = sender_email
    msg['To'] = recipient_email

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        if sender_email and sender_password:
            server.login(sender_email, sender_password)
            server.send_message(msg)
        else:
            print("Email credentials not found in .env!")