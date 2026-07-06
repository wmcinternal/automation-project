import os
import secrets
from datatime import datatime, timedelta
from flask import Flask, request, jsonify, render_template


from audit import init_db, save_audit_transaction, get_ledger_history, get_db_connection
from engine import find_and_lock_pdf, extract_pdf_metrics, run_audit_comparison, generate_audit_report

website=Flask(__name__, template_folder=".")

UPLOAD_FOLDER=./uploads
website.config["UPLOAD_FOLDER"]=UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@website.route("/auth/request-OTP", methods=["POST"])

def request_otp():
    data=request.json or {}
    email=str(data.get("email", "")).lower().strip()

    if not email.endswith("@wmcubehk.com"):
        return jsonify({"status": "error", "message": "Unauthorized domain. Staff email required."}), 403

    otp_code=str(secrets.randbelow(900000)+100000)
    expiry_time=datatime.now()+timedelta(minutes=5)

    conn=get_db_connection()
    cursor=conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT, code TEXT, expires_at DATETIME, is_used INTEGER DEFAULT 0
        )
    """)
    cursor.execute(
        "INSERT INTO login_tokens (email, code, expires_at) VALUES (?, ?, ?)",
        (email, otp_code, expiry_time)
    )

    conn.commit()
    conn.close()

    print(f"📡 OTP [{otp_code}] generated safely and passed to API template wrapper for: {email}")
    return jsonify({"status": "success", "message": "Verification token sent to your inbox."})


@website.route("/auth/verify-otp", methods["POSTS"])
def verify_otp():

    data=request.json or {}
    email=str(data.get("email", "")).lower().strip()
    submitted_code=str(data.get("code", "")).strip()

    conn=get_db_connection()
    cursor=conn.cursor()

    cursor.execute(
        "SELECT code, expires_at, is_used FROM login_tokens WHERE email = ? ORDER BY id DESC LIMIT 1",
        (email,)
    )

    row=cursor.fetchone()

    if not row:
        conn.close()
        return jsonify({"status": "error", "message": "No OTP verified"})

    expires_at = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S.%f")

    if submitted_code!=row["code"]:
        status, msg="error", "Invalid verification token."
    elif datetime.now()>expires_at:
        status, msg="error", "Token has expired."
    elif row["is_used"]==1:
        status, msg="error", "Token has been used."
    else:
        status, msg="success", "Identity confirmed. Access granted"

        cursor.execute("UPDATE login_tokens SET is_used= 1 WHERE email=? AND code=?" (email, code))
        conn.commit()
    conn.close()
    return jsonify({"status": status, "message": msg})


@website.route("/audit/run", methods=["POST"])
def run_compliance_audit():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file detected."}), 400

    excel_file=request.files["file"]
    operator_name=request.form.get("Operator", "Staff Auditor")
    session_ref=f"REF-{secrets.token_hex(4).upper()}"

    saved_excel_path = os.path.join(website.config['UPLOAD_FOLDER'], excel_file.filename)
    excel_file.save(saved_excel_path)

    import pandas as pd
    try:
        df=pd.read_excel(saved_excel_path)
        company_rows=df.to_dict('records')
    except Exception as e:
        return jsonify({"status": "error", "message": f"Invalid or corrupted Excel layout: {e}"}), 400

    engine_results=[]
    for row in company_rows:
        fund_name = str(row.get("Fund Name", ""))
        house_name = str(row.get("Fund House", ""))
        currency = str(row.get("Fund Currency", "")).lower()

        pdf_match = find_and_lock_pdf(house_name, fund_name, folder=".")
        metrics = extract_pdf_metrics(pdf_match, currency, fund_name, folder=".")
        comparison = run_audit_comparison(row, metrics, pdf_match)
        engine_results.append(comparison)
    

    session_meta = {
        "ref_id": session_ref,
        "operator_name": operator_name,
        "file_name": excel_file.filename
    }
    save_audit_transaction(session_meta, engine_results

    return jsonify({
        "status": "success",
        "ref_id": session_ref,
        "audit_data": engine_results
    })

@website.route("/audit/history", methods=["GET"])
def get_audit_history_logs():
    history_logs=get_ledger_history()
    return jsonify({"status": "success", "history": history_logs})

@website.route("/homepage")
def serve_frontend_dashboard():
    return render_template("index.html")

if __name=="__main__":
    init_db()
    print("\n🌍 Unified System Core Operational. Hosting local link: http://127.0.0.1:5000\n")
    website.run(debug=True, port=5000)

