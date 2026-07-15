import os
import time
import resend
import secrets
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file, redirect, Response, abort
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename

from audit import get_db_connection, init_db, save_audit_transaction, get_audit_sessions
from engine import find_and_lock_pdf, extract_pdf_metrics, run_audit_comparison, generate_audit_report

website=Flask(__name__, template_folder=".")

website.secret_key=os.getenv("FLASK_SECRET_KEY")
resend.api_key=os.getenv("RESEND_API_KEY")


limiter=Limiter(
    get_remote_address,
    app=website,
    default_limits=["30 per minute"]
)

def limit_by_email():
    if request.is_json:
        return request.json.get("email", get_remote_address)
    return get_remote_address


UPLOAD_DIR=os.path.dirname(os.path.abspath(__FILE__))
UPLOAD_FOLDER=os.path.join(BAS_DIR, "uploads")
website.config["UPLOAD_FOLDER"]=UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

OUTPUT_DIR=os.path.dirname(os.path.abspath(__file__))
OUTPUT_FOLDER=os.path.join(OUTPUT_DIR, "outputs")
website.config["OUTPUT_FOLDER"]=OUTPUT_FOLDER
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


@website.route("/auth/request-OTP", methods=["POST"])
@limiter.limit("5 per minute", key_func=limit_by_email)
@limiter.limit("10 per minute", key_func=get_remote_address)
def request_otp():
    data=request.json or {}
    email=str(data.get("email", "")).lower().strip()

    if not email.endswith("@wmcubehk.com"):
        return jsonify({"status": "error", "message": "🔒Unauthorized domain. Staff email required."}), 403

    otp_code=str(secrets.randbelow(900000)+100000)
    expiry_time=datetime.now()+timedelta(minutes=5)

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

    try:
        resend.Emails.send({
            "from": "WMCube Gateway <onboarding@resend.dev>",
            "to": [email],
            "subject": "Secure Gateway Login Code",
            "html": f"""
                    <div>
                        <h2> Login Portal OTP </h2>
                        <p> The security code is {otp_code} </p>
                        <p> Enter if it is you. Do not share with others. </p>
                        <p> The code will expiry in 5 minutes. </p>
                        <p> SFC Automation Portal Security Team </p>
                    </div>
                    """
        })
        print(f"📡 OTP [{otp_code}] dispatched successfully...")
        return jsonify({"status": "success"}), 200
    
    except Exception as e:
        print(f"🚨 Critical Failure... : {e}")
        return jsonify({"status": "error"}), 500



@website.route("/auth/verify-otp", methods=["POST"])
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

        cursor.execute("UPDATE login_tokens SET is_used= 1 WHERE email=? AND code=?", (email, submitted_code))
        conn.commit()
    conn.close()
    return jsonify({"status": status, "message": msg})


@website.route("/audit/run", methods=["POST"])
def run_compliance_audit():

    print("⏱️ End-to-End Benchmark Timer Started...")
    start_time=time.time()

    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file detected."}), 400

    
    operator_name=request.form.get("Operator", "Staff Auditor")


    
    session_date=datetime.now().strftime("%Y%m%d")
    session_code=str(secrets.randbelow(900000)+100000)

    session_ref=f"WMC-{session_date}-{session_code}"


    excel_file=request.files["file"]


    safe_file_name=secure_filename(excel_file.filename)

    excel_file_name, extension=os.path.splitext(safe_file_name)
    unique_file=f"{excel_file_name}_{session_ref}{extension}"
    saved_excel_path = os.path.join(website.config['UPLOAD_FOLDER'], unique_file)
    excel_file.save(saved_excel_path)

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
    

    end_time=time.time()
    execution_time=round(end_time-start_time, 2)
    print(f"✅ Total Pipeline Execution Time: {execution_time} seconds.") 


    session_meta = {
        "ref_id": session_ref,
        "operator_name": operator_name,
        "file_name": unique_file,
        "execution_time": execution_time
    }
    save_audit_transaction(session_meta, engine_results)
    audit_file_name=f"audit_report_{session_ref}.xlsx"
    audit_file_path=os.path.join(website.config["OUTPUT_FOLDER"], audit_file_name)

    generate_audit_report(engine_results, audit_file_path)

    return jsonify({
        "status": "success",
        "ref_id": session_ref,
        "audit_data": engine_results,
        "unique_file": unique_file,
        "execution_time": execution_time
    })

@website.route("/audit/history", methods=["GET"])
def get_audit_history_logs():
    history_logs=get_audit_sessions()
    return jsonify({"status": "success", "history": history_logs})

@website.route("/homepage")
def serve_frontend_dashboard():
    return render_template("index.html")


@website.route("/")
def root_auto_redirect():
    return redirect("/homepage")

@website.route("/logo.png.jpeg")
def server_logo():
    return send_from_directory(website.root_path, 'logo.png.jpeg', mimetype='image/png')

@website.route("/favicon.ico.jpeg")
def serve_favicon():
    return send_from_directory(website.root_path, 'favicon.ico.jpeg', mimetype='image/vnd.microsoft.icon')

@website.route("/tas.html")
def serve_tas():
    return send_from_directory(website.root_path, 'tas.html', mimetype='text/html')


@website.route("/audit/download/original/<ref_id>", methods=["GET"])
def download_original_file(ref_id):
    conn=get_db_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT * FROM audit_sessions where ref_id=?", (ref_id,))
    row=cursor.fetchone()
    conn.close()

    if not row or not row['file_name']:
        return "❌ Error: Database record not found.", 404
    
    target_folder=website.config["UPLOAD_FOLDER"]
    target_file=os.path.join(target_folder, row["file_name"])

    if not os.path.exists(target_file):
        return f"❌ Error: The physical file is missing from {file_path}", 404
    
    return send_file(target_file, as_attachment=True, download_name=f"original_file_{ref_id}.xlsx")





@website.route("/audit/download/audit/<ref_id>", methods=["GET"])
def download_audit_file(ref_id):
    output_folder=website.config["OUTPUT_FOLDER"]
    target_file_name=f"audit_report_{ref_id}.xlsx"
    output_path=os.path.join(output_folder, target_file_name)

    if not os.path.exists(output_path):
        return f"❌ Error: The audited Excel file is missing from {output_path}", 404
    

    return send_file(output_path, as_attachment=True, download_name=f"audit_report_{ref_id}.xlsx")


@website.route("/audit/download/receipt/<ref_id>", methods=["GET"])
def download_receipt_file(ref_id):
    
    conn=get_db_connection()
    cursor=conn.cursor()
    cursor.execute("SELECT * FROM audit_sessions WHERE ref_id= ? ", (ref_id,))
    row=cursor.fetchone()
    conn.close()

    if not row:
        return "❌ Error: Audit record not found.", 404

    
    timeUtc=datetime.strptime(row["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
    timeLocal=timeUtc+timedelta(hours=8)

    receipt_text=f"""
                 ====================================================
                 SFC COMPLIANCE AUTOMATION GATEWAY - OFFICIAL RECEIPT
                 ===================================================
                 Transaction Ref ID: {row["ref_id"]}
                 TimeStamp: {timeLocal}
                 Operator Signature: {row["operator_name"]}
                 Original File: {row["file_name"]}
                 Execution Time: {row["execution_time"]}s
                 ====================================================
                """


    return Response(
        receipt_text,
        mimetype="text/plain",
        headers={"Content-disposition": f"attachment; filename=official_receipt_{ref_id}.txt"}
    )


@website.route("/template/download", methods=["GET"])
def template_download():
    template_file=os.path.join(os.getcwd(), "template.xlsx")
    if not os.path.exists(template_file):
        abort(404, description="Demo template file not found on server.")
    return send_file(template_file, as_attachment=True, download_name='failing_demo_template.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    



if __name__=="__main__":
    init_db()
    print("\n🌍 Unified System Core Operational. Hosting local link: http://127.0.0.1:5000\n")
    website.run(debug=True, port=5000)

