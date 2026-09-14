import sqlite3
from fastapi.testclient import TestClient
import app as app_module

# Disable blocking desktop notifications during tests
app_module.trigger_desktop_notification = lambda title, message: None

client = TestClient(app_module.app)

def test_full_web3_and_coin_engine():
    print("\n--- 1. Testing Merchant Balance & Stats Endpoint ---")
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()

    # Get a test merchant ID or create one
    cursor.execute("SELECT merchant_id FROM merchants LIMIT 1")
    row = cursor.fetchone()
    if not row:
        m_id = "TESTM001"
        cursor.execute("""
            INSERT INTO merchants (email, password_hash, bank_account, bank_name, merchant_id, merchant_credits, alerts_remaining, loyalty_points, coin_balance)
            VALUES ('test@example.com', 'hash', '1234567890', 'TestBank', ?, 50, 50, 0, 0.0)
        """, (m_id,))
        conn.commit()
    else:
        m_id = row[0]

    # Reset merchant stats for predictable testing
    cursor.execute("""
        UPDATE merchants 
        SET loyalty_points = 500, coin_balance = 5.0, alerts_remaining = 30, merchant_credits = 30
        WHERE merchant_id = ?
    """, (m_id,))
    conn.commit()
    conn.close()

    # Query merchant details
    res = client.get(f"/api/merchant/{m_id}")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    data = res.json()
    assert data["merchant_id"] == m_id
    assert data["loyalty_points"] == 500
    assert data["coin_balance"] == 5.0
    assert data["alerts_remaining"] == 30
    print(f"Verified initial state for {m_id}: {data}")

    import uuid
    unique_ref = f"TEST-REF-{uuid.uuid4().hex[:8].upper()}"
    alert_payload = {
        "raw_text": "Credit alert: ₦25,000.00 received from John Doe",
        "amount": 25000.0,
        "merchant_id": m_id,
        "reference_number": unique_ref,
        "is_suspicious": False,
        "suspicious_reason": None
    }
    alert_res = client.post("/notifications", json=alert_payload)
    assert alert_res.status_code == 200

    # Verify +10 loyalty points added
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    cursor.execute("SELECT loyalty_points, alerts_remaining FROM merchants WHERE merchant_id = ?", (m_id,))
    pts, rem = cursor.fetchone()
    assert pts == 510, f"Expected 510 points (+10), got {pts}"
    print(f"Points successfully accumulated on verified alert! Current points: {pts}")

    # Verify audit log in coin_transactions
    cursor.execute("SELECT type, points_change, status FROM coin_transactions WHERE merchant_id = ? ORDER BY id DESC LIMIT 1", (m_id,))
    log_row = cursor.fetchone()
    assert log_row[0] == "POINTS_EARNED"
    assert log_row[1] == 10
    print(f"Audit log verified: {log_row}")

    print("\n--- 3. Testing Points-to-Coin Swap Endpoint ---")
    # A) Attempt swap with insufficient points (< 1000)
    swap_fail = client.post("/api/convert-points", json={"merchant_id": m_id, "points": 1000})
    assert swap_fail.status_code == 400
    assert "Insufficient points" in swap_fail.json()["detail"]
    print("Insufficient points validation passed!")

    # B) Give merchant 1,000 points and swap
    cursor.execute("UPDATE merchants SET loyalty_points = 1000 WHERE merchant_id = ?", (m_id,))
    conn.commit()
    conn.close()

    swap_success = client.post("/api/convert-points", json={"merchant_id": m_id, "points": 1000})
    assert swap_success.status_code == 200
    swap_data = swap_success.json()
    assert swap_data["loyalty_points"] == 0
    assert swap_data["coin_balance"] == 15.0 # 5.0 + 10.0
    print(f"Swap successful: {swap_data['message']}")

    print("\n--- 4. Testing In-App Coin Purchase Logic (Buy Alerts) ---")
    # A) Test buying with 10 coins
    buy_res = client.post("/api/buy-alerts-with-coin", json={
        "merchant_id": m_id,
        "package_id": "tier_100",
        "alerts_count": 100,
        "coin_cost": 10.0
    })
    assert buy_res.status_code == 200
    buy_data = buy_res.json()
    assert buy_data["coin_balance"] == 5.0 # 15.0 - 10.0
    assert buy_data["alerts_added"] == 100
    print(f"Alerts purchase successful! Remaining coins: {buy_data['coin_balance']}, Alerts remaining: {buy_data['alerts_remaining']}")

    # B) Test buying when coins are insufficient
    buy_insufficient = client.post("/api/buy-alerts-with-coin", json={
        "merchant_id": m_id,
        "package_id": "tier_100",
        "alerts_count": 100,
        "coin_cost": 10.0
    })
    assert buy_insufficient.status_code == 400
    assert "Insufficient $BMIRROR balance" in buy_insufficient.json()["detail"]
    print("Insufficient coin balance validation passed!")

    print("\n--- 5. Testing Web3 Wallet Linking & Withdrawal ---")
    # A) Invalid address
    bad_link = client.post("/api/link-wallet", json={
        "merchant_id": m_id,
        "wallet_address": "invalid_address"
    })
    assert bad_link.status_code == 400
    print("Invalid wallet address rejected as expected.")

    # B) Valid Polygon address
    valid_address = "0x71C841032A5cB3791C486E74537774415064f2B2"
    good_link = client.post("/api/link-wallet", json={
        "merchant_id": m_id,
        "wallet_address": valid_address
    })
    assert good_link.status_code == 200
    print("Valid wallet linked successfully!")

    # C) Withdrawal
    withdraw_res = client.post("/api/withdraw-coins-to-wallet", json={
        "merchant_id": m_id,
        "wallet_address": valid_address,
        "amount": 5.0
    })
    assert withdraw_res.status_code == 200
    w_data = withdraw_res.json()
    assert w_data["coin_balance"] == 0.0 # 5.0 - 5.0
    assert "tx_details" in w_data
    print(f"Withdrawal succeeded! Tx Hash: {w_data['tx_details']['tx_hash']}")

    print("\n[SUCCESS] ALL WEB3 & COIN ENGINE TEST CASES PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_full_web3_and_coin_engine()
