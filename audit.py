import sqlite3

DB_FILE="audit.db"

def get_db_connection():
    conn=sqlite3.connect(DB_FILE)
    conn.row_factory=sqlite3.Row

    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            ref_id TEXT UNIQUE,
            operator_name TEXT,
            file_name TEXT,
            overall_status TEXT

        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ref_id TEXT,
            fund_house TEXT,
            fund_name TEXT,
            currency TEXT,
            matched_pdf TEXT,
            dividend_check_status TEXT,
            min_int_status TEXT,
            min_sub_status TEXT,
            FOREIGN KEY (ref_id) REFERENCES audit_sessions(ref_id)
        )
    """)

    conn.commit()
    conn.close()
    print("🗄️ Relational SQLite storage matrix initialized completely.")



def save_audit_transaction(session_meta: dict, engine_results_list: list):

    conn=get_db_connection()
    cursor=conn.cursor()

    overall_status="Pass"
    for engine_result in engine_results_list:
        if "🔴" in str(engine_result.get("Dividend Check", "")) or \
           "🔴" in str(engine_result.get("Min Int Amt Check", "")) or \ 
           "🔴" in str(engine_result.get("Min Sub Amt Check", "")):
           overall_status="Fail"
           break


    cursor.execute("""
        INSERT INTO audit_sessions (
            ref_id, operator_name, file_name, overall_status,
        ) VALUES (?, ?, ?, ?)
    """, (
        session_meta.get("ref_id"),
        session_meta.get("operator_name"),
        session_meta.get("file_name"),
        overall_status

    ))

    for engine_result in engine_results_list:
        cursor.execute("""
            INSERT INTO audit_records (
                ref_id, fund_house, fund_name, currency, matched_pdf,
                dividend_check_status, min_int_status, min_sub_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_meta.get("ref_id"),
            engine_result.get("Management Company"),
            engine_result.get("Target Fund"),
            engine_result.get("Currency"),
            engine_result.get("Matched PDF"),
            engine_result.get("Dividend Check"),
            engine_result.get("Min Int Amt Check"),
            engine_result.get("Min Sub Amt Check")
        ))

    

    conn.commit()
    conn.close()


def get_audit_sessions(target_ref_id: str=None):

    conn=get_db_connection()
    cursor=conn.cursor()

    if target_ref_id:
        cursor.execute("SELECT * FROM audit_sessions WHERE ref_id= ?", (target_ref_id,))
    else:
        cursor.execute("SELECT * FROM audit_sessions ORDER BY timestamp DESC")

    rows=cursor.fetchall()

    cursor.close()

    return [dict(row) for row in rows]






