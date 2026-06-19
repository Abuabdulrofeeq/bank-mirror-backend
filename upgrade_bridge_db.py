import sqlite3

def upgrade():
    conn = sqlite3.connect('bank_mirror.db')
    cursor = conn.cursor()
    
    # Add new columns to transactions table if they don't exist
    try:
        cursor.execute("ALTER TABLE transactions ADD COLUMN reference_number TEXT")
        print("Added reference_number column.")
    except sqlite3.OperationalError:
        print("reference_number column might already exist.")
        
    try:
        cursor.execute("ALTER TABLE transactions ADD COLUMN is_suspicious BOOLEAN DEFAULT 0")
        print("Added is_suspicious column.")
    except sqlite3.OperationalError:
        print("is_suspicious column might already exist.")
        
    try:
        cursor.execute("ALTER TABLE transactions ADD COLUMN suspicious_reason TEXT")
        print("Added suspicious_reason column.")
    except sqlite3.OperationalError:
        print("suspicious_reason column might already exist.")
        
    conn.commit()
    conn.close()
    print("Database upgrade complete.")

if __name__ == "__main__":
    upgrade()
