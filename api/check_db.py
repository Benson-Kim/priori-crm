import psycopg2

try:
    conn = psycopg2.connect(
        "postgresql://priori:priori_dev_123@localhost:5432/prioritech"
    )
    print("Connection successful")
    conn.close()
except Exception as e:
    print(f"Connection failed: {e}")
