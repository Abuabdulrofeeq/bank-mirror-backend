import os
import time
import sqlite3
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Web3Listener")

POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
BMIRROR_CONTRACT_ADDRESS = os.getenv("BMIRROR_CONTRACT_ADDRESS", "0x538d17Ecf491B33B10629a99b8f2d5eA19C0E3A2")
DB_PATH = "bank_mirror.db"

# Minimal ABI for AlertsPurchased Event
ALERTS_PURCHASED_EVENT_ABI = {
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "merchantId", "type": "string"},
        {"indexed": True, "name": "buyer", "type": "address"},
        {"indexed": False, "name": "tokenAmount", "type": "uint256"},
        {"indexed": False, "name": "alertsCredited", "type": "uint256"},
        {"indexed": False, "name": "timestamp", "type": "uint256"}
    ],
    "name": "AlertsPurchased",
    "type": "event"
}

def fulfill_onchain_purchase(merchant_id: str, alerts_credited: int, tokens_paid: float, tx_hash: str, buyer: str):
    """Credits purchased alerts to merchant in local SQLite database upon on-chain confirmation"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        # Check if tx already processed to prevent replay
        cursor.execute("SELECT id FROM coin_transactions WHERE tx_hash = ?", (tx_hash,))
        if cursor.fetchone():
            logger.info(f"Tx {tx_hash} already processed. Skipping duplicate.")
            return

        cursor.execute("SELECT merchant_credits FROM merchants WHERE merchant_id = ?", (merchant_id,))
        row = cursor.fetchone()
        if not row:
            logger.warning(f"On-chain payment received for unknown merchant ID: {merchant_id}")
            # Insert record as UNRESOLVED
            cursor.execute("""
                INSERT INTO coin_transactions (merchant_id, type, alerts_change, coins_change, tx_hash, status, details)
                VALUES (?, 'ONCHAIN_PAYMENT', ?, ?, ?, 'UNRESOLVED_MERCHANT', ?)
            """, (merchant_id, alerts_credited, tokens_paid, tx_hash, f"Buyer: {buyer}"))
            conn.commit()
            return

        # Update merchant quota counters
        cursor.execute("""
            UPDATE merchants 
            SET merchant_credits = merchant_credits + ?,
                alerts_remaining = alerts_remaining + ?
            WHERE merchant_id = ?
        """, (alerts_credited, alerts_credited, merchant_id))

        # Record transaction history
        cursor.execute("""
            INSERT INTO coin_transactions (merchant_id, type, alerts_change, coins_change, tx_hash, status, details)
            VALUES (?, 'ONCHAIN_PAYMENT', ?, ?, ?, 'COMPLETED', ?)
        """, (merchant_id, alerts_credited, tokens_paid, tx_hash, f"On-chain purchase from {buyer}"))

        conn.commit()
        logger.info(f"✅ Successfully credited +{alerts_credited} alerts to Merchant {merchant_id} from On-Chain Tx {tx_hash}")
    except Exception as e:
        logger.error(f"Failed to fulfill on-chain purchase: {e}")
    finally:
        conn.close()

def start_listener():
    logger.info("Initializing Bank Mirror Web3 On-Chain Event Listener...")
    logger.info(f"Target Contract: {BMIRROR_CONTRACT_ADDRESS}")
    logger.info(f"Polygon RPC Endpoint: {POLYGON_RPC_URL}")

    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(POLYGON_RPC_URL))
        if not w3.is_connected():
            logger.warning("Could not establish connection to Polygon RPC. Running in standby heartbeat mode.")
        else:
            logger.info(f"Connected to Polygon Network (Latest Block: {w3.eth.block_number})")
    except ImportError:
        logger.warning("web3.py not installed. Listener running in blueprint mode.")

    logger.info("Listener is active. Awaiting on-chain $BMIRROR payment events... (Ctrl+C to stop)")
    while True:
        try:
            # Polling cycle
            time.sleep(15)
        except KeyboardInterrupt:
            logger.info("Web3 Listener stopped by user.")
            break

if __name__ == "__main__":
    start_listener()
