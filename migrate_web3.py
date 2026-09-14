import sqlite3

def migrate():
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    print("Beginning migration on bank_mirror.db...")

    # 1. Expand merchants table columns
    columns_to_add = [
        ("loyalty_points", "INTEGER DEFAULT 0"),
        ("coin_balance", "REAL DEFAULT 0.0"),
        ("alerts_remaining", "INTEGER DEFAULT 0"),
        ("web3_wallet_address", "TEXT DEFAULT NULL")
    ]

    cursor.execute("PRAGMA table_info(merchants)")
    existing_columns = [col[1] for col in cursor.fetchall()]
    print(f"Existing merchants columns: {existing_columns}")

    for col_name, col_type in columns_to_add:
        if col_name not in existing_columns:
            try:
                cursor.execute(f"ALTER TABLE merchants ADD COLUMN {col_name} {col_type}")
                print(f"Added column: {col_name} ({col_type})")
            except Exception as e:
                print(f"Error adding column {col_name}: {e}")
        else:
            print(f"Column {col_name} already exists.")

    # 2. Sync alerts_remaining with merchant_credits for existing merchants
    try:
        cursor.execute("UPDATE merchants SET alerts_remaining = merchant_credits WHERE (alerts_remaining IS NULL OR alerts_remaining = 0) AND merchant_credits > 0")
        print(f"Synchronized alerts_remaining with merchant_credits. Rows updated: {cursor.rowcount}")
    except Exception as e:
        print(f"Sync error: {e}")

    # 3. Create coin_transactions table
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
    print("Verified/Created coin_transactions table.")

    conn.commit()
    conn.close()
    print("Migration completed successfully!")

if __name__ == "__main__":
    migrate()
