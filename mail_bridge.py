import sys
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
import imaplib
import email
import sqlite3
import re
import time
import requests
import html
import socket

def extract_text_from_html(html_content):
    # Remove HTML tags and replace with space
    text = re.sub(r'<[^>]+>', ' ', html_content)
    # Decode HTML entities like &nbsp;
    text = html.unescape(text)
    # Remove multiple spaces/newlines
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

EMAIL_USER = "bankmirror8@gmail.com"
EMAIL_PASS = "vtdyfbzpkrtfrryc" # No spaces here!
BACKEND_URL = "https://bank-mirror-backend-1.onrender.com/notifications"
MERCHANT_ID = "B1D71377"

def verify_email_authenticity(msg):
    # 1. Header Inspection
    from_header = msg.get("From", "")
    return_path = msg.get("Return-Path", "")
    
    # Extract email addresses from headers
    from_match = re.search(r'<([^>]+)>', from_header)
    from_email = from_match.group(1) if from_match else from_header.strip()
    
    return_match = re.search(r'<([^>]+)>', return_path)
    return_email = return_match.group(1) if return_match else return_path.strip()
    
    from_domain = from_email.split('@')[-1].lower() if '@' in from_email else ""
    return_domain = return_email.split('@')[-1].lower() if '@' in return_email else ""
    
    if from_domain and return_domain and from_domain != return_domain:
        print(f"[WARNING] Domain mismatch: From ({from_domain}) != Return-Path ({return_domain}). Checking DKIM/SPF...")
        
    # 2. SPF/DKIM/DMARC Checks
    auth_results = msg.get("Authentication-Results", "").lower()
    if auth_results:
        # If the header exists, we expect it to pass
        if "spf=pass" not in auth_results and "dkim=pass" not in auth_results:
            return False, "Failed SPF/DKIM checks"
            
    return True, "Valid"

def heuristic_check(body):
    # 1. Grammar & Character Audit (Zero instead of O in bank names)
    if re.search(r'(?i)(gtb|zenith|uba|firstbank|access|polaris|fcmb|stanbic)[^a-z\s]*0', body) or re.search(r'[a-zA-Z]0[a-zA-Z]', body):
        return False, "Suspicious characters (0 instead of O) detected"
        
    # 2. The "Urgency" Filter
    urgency_keywords = [r'urgent', r'final warning', r'call this number', r'immediate action']
    for keyword in urgency_keywords:
        if re.search(keyword, body, re.IGNORECASE):
            return False, f"Urgency keyword detected: {keyword}"
            
    return True, "Valid"

def extract_and_check_reference(body):
    # Try to extract a reference number
    ref_match = re.search(r'(?i)\b(?:ref|reference|txn id|trx|transaction|session id|receipt no|order no)\b[\s\:\-]*([A-Za-z0-9]{6,25})', body)
    if not ref_match:
        return None, False, "Missing reference number"
        
    reference_number = ref_match.group(1)
    
    return reference_number, True, "Valid"

def send_alert_to_backend(raw, amount, merchant_id, reference_number, is_suspicious, suspicious_reason):
    payload = {
        "raw_text": raw,
        "amount": float(amount.replace(',', '')),
        "merchant_id": merchant_id,
        "reference_number": reference_number,
        "is_suspicious": is_suspicious,
        "suspicious_reason": suspicious_reason
    }
    try:
        response = requests.post(BACKEND_URL, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"[SUCCESS] Successfully sent to backend: {response.json()}")
        else:
            print(f"[FAILED] Failed to send to backend: {response.status_code} {response.text}")
    except Exception as e:
        print(f"[ERROR] Connection error to backend: {e}")

def process_email(msg, raw_body):
    body = raw_body
    print(f"🆕 New Alert Received! Subject: {msg.get('Subject', 'No Subject')}")
    
    amount_match = re.search(r"(?:NGN|₦|N|NG|Naira)\s?([\d,]+(?:\.\d+)?)", body, re.IGNORECASE)
    if amount_match:
        amount = amount_match.group(1)
        
        is_valid = True
        suspicious_reason = ""
        
        # 1. Digital ID Check
        auth_pass, auth_reason = verify_email_authenticity(msg)
        if not auth_pass:
            is_valid = False
            suspicious_reason += f"[Header] {auth_reason}. "
            
        # 2. Vibe Check
        heur_pass, heur_reason = heuristic_check(body)
        if not heur_pass:
            is_valid = False
            suspicious_reason += f"[Heuristic] {heur_reason}. "
            
        # 3. Transaction ID Check
        ref_num, ref_pass, ref_reason = extract_and_check_reference(body)
        
        if not ref_pass:
            is_valid = False
            suspicious_reason += f"[Reference] {ref_reason}. "
        
        if not is_valid:
            print(f"[SUSPICIOUS] SUSPICIOUS ALERT BLOCKED! Amount: {amount}")
            print(f"Reason: {suspicious_reason}")
            send_alert_to_backend(body, amount, MERCHANT_ID, ref_num, True, suspicious_reason)
        else:
            print(f"[VALID] Valid Alert Detected! Amount: {amount}")
            send_alert_to_backend(body, amount, MERCHANT_ID, ref_num, False, "Valid")
            print("[INFO] Alert pushed to backend for processing.")
    else:
        print("[INFO] No amount found in email. Ignoring.")

def idle(connection):
    # Set timeout so IDLE doesn't hang indefinitely if connection drops
    connection.sock.settimeout(600)
    tag = connection._new_tag()
    name = bytes('IDLE', 'ASCII')
    data = tag + b' ' + name + b'\r\n'
    connection.send(data)
    
    response = connection.readline()
    if response != b'+ idling\r\n' and not response.startswith(b'+'):
        raise Exception(f"IDLE not supported or unexpected response: {response}")
        
    try:
        while True:
            line = connection.readline()
            if not line:
                break
            if b'EXISTS' in line or b'RECENT' in line:
                break
    except socket.timeout:
        pass # 10 mins elapsed, refresh IDLE

    # Exit IDLE mode
    connection.send(b'DONE\r\n')
    try:
        while True:
            line = connection.readline()
            if line.startswith(tag):
                break
    except socket.timeout:
        pass
    # Reset timeout
    connection.sock.settimeout(None)

def start_email_listener():
    print("🚀 Bank Mirror Email Listener is starting up...")
    
    while True:
        try:
            # Connect and log in to the mailbox
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(EMAIL_USER, EMAIL_PASS)
            mail.select("inbox")
            
            print("🔒 Securely connected to Mailbox. Listening for live alerts via IMAP IDLE...")
            
            while True:
                # First, fetch any unseen messages
                status, messages = mail.search(None, 'UNSEEN')
                if status == 'OK' and messages[0]:
                    for num in messages[0].split():
                        status, data = mail.fetch(num, '(RFC822)')
                        if status != 'OK': continue
                        msg = email.message_from_bytes(data[0][1])
                        
                        body = ""
                        html_body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                content_type = part.get_content_type()
                                if content_type == "text/plain":
                                    payload = part.get_payload(decode=True)
                                    if payload:
                                        body += payload.decode('utf-8', 'ignore') + " "
                                elif content_type == "text/html":
                                    payload = part.get_payload(decode=True)
                                    if payload:
                                        html_body += payload.decode('utf-8', 'ignore') + " "
                            if not body.strip() and html_body:
                                body = extract_text_from_html(html_body)
                        else:
                            payload = msg.get_payload(decode=True)
                            if payload:
                                content = payload.decode('utf-8', 'ignore')
                                if msg.get_content_type() == "text/html":
                                    body = extract_text_from_html(content)
                                else:
                                    body = content
                        
                        process_email(msg, body)
                
                # Wait for new messages using IDLE (blocking wait until something changes)
                idle(mail)
                
        except Exception as e:
            print(f"⚠️ Connection dropped or error occurred: {e}")
            print("🔄 Reconnecting in 10 seconds...")
            time.sleep(10)

if __name__ == "__main__":
    start_email_listener()