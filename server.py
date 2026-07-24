import os
import io
import time
import json
import resend
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import zipfile
import pandas as pd
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file, redirect, Response, abort, stream_with_context, render_template_string, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename

from audit import get_db_connection, init_db, save_audit_transaction, get_audit_sessions
from engine import webscrap_sfc_pdf, load_target_data, extract_pdf_metrics, run_audit_comparison, generate_audit_report

website=Flask(__name__, template_folder=".")

website.secret_key=os.getenv("FLASK_SECRET_KEY")
website.config["PERMANENT_SESSION_LIFETIME"]=timedelta(minutes=10)
website.config["SESSION_COOKIE_HTTPONLY"]=True
website.config["SESSION_COOKIE_SAMESITE"]='Lax'

resend.api_key=os.getenv("RESEND_API_KEY")

SENDER_EMAIL=os.getenv("OUTLOOK_EMAIL")
SENDER_PASSWORD=os.getenv("MICROSOFT_APP_PASSWORD")
ADMIN_ACCESS=os.getenv("ADMIN_ROOT_PASSWORD")


LOGIN_INTERFACE="""
<!DOCTYPE html>
<html>
<head>
    <title>WMC</title>
    <style>
        body { background: #000000; color: #ffffff; font: 1.2rem monospace; font-family: 'Segoe UI'; text-align: center; padding-top: 35vh; }
        input { background: transparent; border: none; border-bottom: 2px solid #38bdf8; color: #38bdf8; font: 1.1rem monospace; outline: none; text-align: center; width: 250px; }
        input::placeholder {color: #64748b; font-size: 0.85rem;}
        p { color: #ef4444; font-size: 0.9rem; font-weight: bold; margin-top: 15px;}
    </style>
</head>
<body>
    LOGIN to SFC Compliance Automation Portal <br><br>

    <input type="email" id="email_input" autofocus placeholder="Enter email + Press Enter">

    <input type="text" id="otp_input" maxlength="6" placeholder="Enter OTP + Press Enter" style="display:none;">

    <p id="error_msg"></p>

    
    
    <script>

        if (sessionStorage.getItem("user_is_authenticated")==="true") {
            sessionStorage.clear();
        }
        
        setInterval(() => {
            let loginTime=sessionStorage.getItem("login_time");
            if (loginTime){
                if (Date.now()-parseInt(loginTime)>600000){
                    sessionStorage.clear();
                    window.location.replace("/");
                    }
                }
                
                
        }, 5000);

        if (sessionStorage.getItem("user_is_authenticated") === "true") {
            window.location.href = "/homepage";
        }


        let saved_email = "";

        document.getElementById("email_input").addEventListener("keydown", function(event) {
            if (event.key==="Enter"){
                const input = document.getElementById("email_input");
                saved_email=input.value.trim();

                if (!saved_email) return;

                document.getElementById("error_msg").innerText = "";

                fetch("/auth/request-OTP", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email: saved_email })
                })

                .then(response => response.json())
                .then(data => {
                    if (data.status==="success") {
                        if (data.bypass) {
                            sessionStorage.setItem("user_is_authenticated", "true");
                            sessionStorage.setItem("login_time", Date.now());
                            window.location.href = "/homepage";
                            return;
                        }

                        input.style.display="none";
                        const otp_place=document.getElementById("otp_input");
                        otp_place.style.display="inline-block";
                        otp_place.focus();
                    }
                    else {
                        document.getElementById("error_msg").innerText = data.message || "Failed";
                    }
                
                })
                .catch(() => document.getElementById("error_msg").innerText = "Network Error");




            }
        });

        document.getElementById("otp_input").addEventListener("keydown", function(event) {
            if (event.key==="Enter") {
                otp_typed=document.getElementById("otp_input");
                otp_received=otp_typed.value.trim();
                if (!otp_received) return;

                otp_typed.disabled=true;

                document.getElementById("error_msg").innerText = "";

                fetch("/auth/verify-otp", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email: saved_email, code: otp_received })
                })

                .then(response => response.json())
                .then(data => {
                    
                    if (data.status=="success") {
                        sessionStorage.setItem("user_is_authenticated", "true");
                        sessionStorage.setItem("login_time", Date.now());
                        window.location.href="/homepage";
                    }
                    else {
                        otp_typed.disabled=false;
                        document.getElementById("error_msg").innerText = data.message || "Invalid";
                    }

                    
                })


                .catch(() => {
                    otp_typed.disabled=false;
                    document.getElementById("error_msg").innerText = "Verification Error";
                });
            }

        });
    </script>
</body>
</html>
"""





@website.before_request
def enforce_global_authentication():


    public_endpoints = [
        "request_otp", 
        "verify_otp", 
        "root_login_gate",
        "server_logo",
        "serve_favicon",
        "static",
        "session_timeout"
    ]

    if request.endpoint in public_endpoints or request.endpoint is None:
        return

    if "user_email" not in session or request.args.get("tab_auth")=="false":
        return render_template_string(LOGIN_INTERFACE)



limiter=Limiter(
    get_remote_address,
    app=website,
    default_limits=["30 per minute"]
)

def limit_by_email():
    if request.is_json:
        return request.json.get("email", get_remote_address)
    return get_remote_address


UPLOAD_DIR=os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER=os.path.join(UPLOAD_DIR, "uploads")
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

    if email==ADMIN_ACCESS:
        session.clear()
        session["user_email"]=email
        session.permanent=True
        return jsonify({"status": "success", "bypass": True}), 200

    elif not (email.endswith("@wmcubehk.com") or email=="wmcinternal@hotmail.com"):
        return jsonify({"status": "error", "message": "🔒Unauthorized domain. Staff email required."}), 403

    otp_code=str(secrets.randbelow(900000)+100000)
    expiry_time=datetime.now()+timedelta(minutes=5)

    conn=get_db_connection()
    cursor=conn.cursor()
    
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
                        <p> The security code is <b>{otp_code}</b> </p>
                        <p> Enter this code only if you initiated the login request. </p>
                        <p>Please do not share it with anyone — our team will never ask you for this code.</p>
                        <p> The code will expire in 5 minutes. </p>
                        <p>If you did not attempt to sign in, you can safely ignore this email or contact our security team.</p>
                        <p> </p>
                        <p>Thank you</p>
                        <p> WMCube SFC Portal Security Team</p>
                    </div>
                    """
        })
        print(f"📡 OTP [{otp_code}] dispatched successfully via Resend Sandbox...")
        return jsonify({"status": "success"}), 200
    
    except Exception as e:
        print(f"🚨 Resend Sandbox Failure: {e}")
        return jsonify({"status": "error", "message": f"Resend Error: {str(e)}"}), 500


@website.route("/auth/verify-otp", methods=["POST"])
@limiter.limit("5 per minute", key_func=limit_by_email)
@limiter.limit("15 per minute", key_func=get_remote_address)
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

    if submitted_code==ADMIN_ACCESS:
        status, msg="success", "Identity confirmed. Access granted"
    elif submitted_code!=row["code"]:
        status, msg="error", "Invalid verification token."
    elif datetime.now()>expires_at:
        status, msg="error", "Token has expired."
    elif row["is_used"]==1:
        status, msg="error", "Token has been used."
    else:
        status, msg="success", "Identity confirmed. Access granted"

        cursor.execute("UPDATE login_tokens SET is_used= 1 WHERE email=? AND code=?", (email, submitted_code))
        conn.commit()
        session.clear()
        session["user_email"]=email

        session.permanent=True

    if status=="success":

        resend.Emails.send({
                "from": "WMCube Gateway <onboarding@resend.dev>",
                "to": [email],
                "subject": "Successful Login Notification",
                "html": f"""
                    <div>
                    <h2>Login Successful</h2>
                    <p>Your identity has been successfully verified and access to the WMCube SFC Portal has been granted.</p>
                    <p>Account: <b>{email}</b></p>
                    <p>Login Time: <b>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</b></p>
                    <p>If this sign-in was initiated by you, no further action is required.</p>
                    <p>If you do not recognize this activity, please contact our security team immediately and reset your account credentials.</p>
                    <p></p>
                    <p>Thank you</p>
                    <p>WMCube SFC Portal Security Team</p>  
                    </div>
                    """
            })
    else:

        resend.Emails.send({
                "from": "WMCube Gateway <onboarding@resend.dev>",
                "to": [email],
                "subject": "Failed Login Notification",
                "html": f"""
                    <div>
                        <h2>Failed Login Attempt Detected</h2>
                        <p>A login verification attempt for your account was unsuccessful.</p>
                        <p>Reason: <b>{msg}</b></p>
                        <p>Attempt Time: <b>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</b></p>
                        <p>If this was you, please ensure that you entered the correct verification code before it expired.</p>
                        <p>If you did not initiate this login attempt, please review your account security and contact our security team if necessary.</p>
                        <p></p>
                        <p>Thank you</p>
                        <p>WMCube SFC Portal Security Team</p>
                    </div>
                    """
            })
                

    


    conn.close()
    return jsonify({"status": status, "message": msg})


@website.route("/auth/logout", methods=["GET", "POST"])
def session_timeout():
    session.clear()
    return redirect("/")

@website.route("/audit/run", methods=["POST"])
def run_compliance_audit():

    def generate_events():

        yield "data: 🔋 Initialization complete. Automation core is online...\n\n"
    
        start_time=time.time()
        operator_name=request.form.get("Operator", "Staff Auditor")
        session_date=datetime.now().strftime("%Y%m%d")
        session_code=str(secrets.randbelow(900000)+100000)
        session_ref=f"WMC-{session_date}-{session_code}"


        if 'file' not in request.files or operator_name=="User":
            yield "data: 📂 Running Developer Sandbox Mode (Using template_golden.xlsx)\n\n"
            golden_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "template_golden.xlsx")
            if os.path.exists(golden_path):
                saved_excel_path=golden_path
                unique_file="template_golden.xlsx"
            else: 
                yield "data: ERROR: No file detected.\n\n"
                return
        else:
            yield "data: 🚀 Reading system worksheet records...\n\n"
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
            yield f"data: ERROR: Failed reading Excel spreadsheet: {e}\n\n"
            return

        engine_results=[]
        total_rows=len(company_rows)
        yield f"data: 🤖 Processing target ledger. Found {total_rows} audit rows...\n\n"
    

        for index, row in enumerate(company_rows):

            ce_no=row.get("SFC Sub-fund CE No.")
            fund_name = str(row.get("Fund Name", ""))
            yield f"data: ⚙️ Processing Row {index+1}/{total_rows}: Scraping SFC registry for '{fund_name[:25]}'...\n\n"
        
            pdf_match = webscrap_sfc_pdf(ce_no, folder="webscrap")
            pdf_filename=os.path.basename(pdf_match) if pdf_match else None
            yield f"data: 📥 Row {index+1}/{total_rows}: Scraping complete. Extracting target PDF metrics...\n\n"       
        
            currency=str(row.get("Fund Currency", "")).strip().lower()
            metrics = extract_pdf_metrics(pdf_filename, currency, fund_name, folder="webscrap")
            comparison = run_audit_comparison(row, metrics, pdf_filename)
            yield f"data: 📊 Row {index+1}/{total_rows}: Comparing registry records with live database values...\n\n"
            
            engine_results.append(comparison)
    
        yield "data: 🗄️ Packaging evaluation ledger transactions & writing system reports...\n\n"
        end_time=time.time()
        execution_time=round(end_time-start_time, 2)
        yield f"data: ✅ Total Pipeline Execution Time: {execution_time} seconds.\n\n" 


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

        payload={
            "status": "success",
            "ref_id": session_ref,
            "audit_data": engine_results,
            "unique_file": unique_file,
            "execution_time": execution_time
        }

        yield f"data: DONE: {json.dumps(payload)}\n\n"

    response=Response(stream_with_context(generate_events()), content_type='text/event-stream')
    response.headers['X-Accel-Buffering']='no'    
    return response



@website.route("/audit/history", methods=["GET"])
def get_audit_history_logs():
    history_logs=get_audit_sessions()
    return jsonify({"status": "success", "history": history_logs})

@website.route("/homepage")
def serve_frontend_dashboard():

    return render_template("index.html")


@website.route("/")
def root_login_gate():
    if "user_email" in session:
        return redirect("/homepage")
    return render_template_string(LOGIN_INTERFACE)

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
        return f"❌ Error: The physical file is missing from {target_file}", 404
    
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


@website.route("/audit/download/webscrap/<ref_id>", methods=["GET"])
def download_webscrap_source(ref_id):

    webscrap_source=os.path.join(os.path.dirname(os.path.abspath(__file__)), "webscrap")
    if not os.path.exists(webscrap_source) or not os.listdir(webscrap_source):
        return "❌ Error: The webscrap folder is empty or missing.", 404

    webscrap_stream=io.BytesIO()

    with zipfile.ZipFile(webscrap_stream, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(webscrap_source):
            for file in files:
                if file.endswith("KPS.pdf"):
                    file_source=os.path.join(root, file)
                    zipf.write(file_source, arcname=file)
    
    webscrap_stream.seek(0)

    return send_file(
        webscrap_stream, 
        mimetype="application/zip", 
        as_attachment=True, 
        download_name=f"webscrap_source_{ref_id}.zip"
    )
    




@website.route("/template/download", methods=["GET"])
def template_download():
    template_file=os.path.join(os.getcwd(), "template_golden.xlsx")
    if not os.path.exists(template_file):
        abort(404, description="Demo template file not found on server.")
    return send_file(template_file, as_attachment=True, download_name='template_golden.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    



if __name__=="__main__":
    init_db()
    print("\n🌍 Unified System Core Operational. Hosting local link: http://127.0.0.1:5000\n")
    website.run(debug=True, port=5000)

