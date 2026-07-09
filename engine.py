import os
import re
import pandas as pd
from rapidfuzz import fuzz
import pdfplumber

print("\n🚀 Booting up Solid Backend MVP (Read -> Compare -> Write Mode)...\n")


def load_target_data(excel_path):
    try:
        print(f"📂 Loading target instructions from {excel_path}...")
        df=pd.read_excel(excel_path)
        return df.to_dict('records')
    except FileNotFoundError:
        print(f"❌ CRITICAL ERROR: Could not find '{excel_path}' in this folder!")
        exit()


def find_and_lock_pdf(target_company_name, target_fund_name, folder="."):

    normalized_name = target_fund_name.lower().replace("-", " ").replace("–", " ")
    excel_words = [w.strip() for w in normalized_name.split() if len(w.strip()) > 1]

    skip_fillers = ["usd", "aud", "hkd", "acc", "hedged", "hdg", "fcp", "funds"]
    core_excel_words = [w for w in excel_words if w not in skip_fillers]

    if not core_excel_words:
        return None
    
    company_keyword = target_company_name.split()[0].lower() if target_company_name else ""
    
    for filename in os.listdir(folder):
        if filename.lower().endswith(".pdf"):
            try:
                with pdfplumber.open(os.path.join(folder, filename)) as pdf:
                    flattened_pdf_text = " ".join(pdf.pages[0].extract_text().lower().split())
                    
                    matched_count = sum(1 for word in core_excel_words if word in flattened_pdf_text)
                    match_percentage = matched_count / len(core_excel_words)


                    if company_keyword and (company_keyword in flattened_pdf_text) and (match_percentage>=0.70):
                        return filename
            except:
                continue

    for filename in os.listdir(folder):
        if filename.lower().endswith(".pdf"):
            try:
                with pdfplumber.open(os.path.join(folder, filename)) as pdf:
                    flattened_pdf_text = " ".join(pdf.pages[0].extract_text().lower().split())

                    matched_count = sum(1 for word in core_excel_words if word in flattened_pdf_text)
                    match_percentage = matched_count / len(core_excel_words)

                    if  match_percentage>=0.7:
                        return filename
            except:
                continue

    return None
    



def extract_pdf_metrics(matched_pdf, target_currency, target_fund, folder="."):

    engine_data = {"Company": "NOT FOUND", "Min Int Amt": 0.0, "Min Sub Amt": 0.0}

    flat_fund_local = target_fund.lower().replace("-", " ")
    class_list = ["a2", "aa", "at", "b2", "c2", "i2", "w2", "bt", "ct", "it", "ia", "wt", "a", "b", "c", "i"]
    share_class_target = next((token for token in flat_fund_local.split() if token in class_list), "a")


    if not matched_pdf:
        return engine_data

    try:
        with pdfplumber.open(os.path.join(folder, matched_pdf)) as pdf:
            pdf_text=pdf.pages[0].extract_text().lower()
            
            lines = pdf_text.split("\n")
            
            for idx, line in enumerate(lines):
                
                if "min. investment" in line or "minimum investment" in line:
                    
                    for lookahead_offset in range(1, 11):
                        if idx + lookahead_offset >= len(lines):
                            break  

                        target_line = lines[idx + lookahead_offset]
                        
                        is_class_match = (
                            "classes a" in target_line or 
                            "class a" in target_line or 
                            (share_class_target == "aa" and "aa" in target_line.replace(",", " ").split())
                        )
                        
                        if is_class_match:
                            found_initial=False
                            
                            for number_offset in range(0, 5):
                                if idx + lookahead_offset + number_offset >= len(lines):
                                    break
                                num_line = lines[idx + lookahead_offset + number_offset]
                                

                                active_currency = target_currency
                                if target_currency not in num_line and "usd" in num_line:
                                    active_currency = "usd"
                                    
                                if active_currency in num_line:
                                    
                                    all_amounts = re.findall(rf"{active_currency}\s*([0-9][\d,.]*)", num_line)
                                    
                                    if all_amounts:
                                        
                                        values = [float(val.replace(",", "")) for val in all_amounts]
                                        
                                        if len(values) >= 2:
                                            engine_data["Min Int Amt"] = values[0]
                                            engine_data["Min Sub Amt"] = values[1]
                                            break
                                            
                                        else:
                                            amount_value = values[0]
                                            if not found_initial and "subsequent" not in num_line and "additional" not in num_line:
                                                engine_data["Min Int Amt"] = amount_value
                                                found_initial = True
                                            elif found_initial or "subsequent" in num_line or "additional" in num_line:
                                                engine_data["Min Sub Amt"] = amount_value
                                                break



                    if engine_data["Min Int Amt"] > 0.0 or "none" in line:
                        break
    
    except Exception as e:
        print(f"⚠️ Error scraping inner text details from {matched_pdf}: {e}")


    return engine_data





def run_audit_comparison(staff_row, engine_data, matched_pdf):

    staff_house = str(staff_row.get("Fund House", ""))
    fund_name_target = str(staff_row.get("Fund Name", ""))
    currency_target = str(staff_row.get("Fund Currency", "")).upper()
    staff_amt = float(staff_row.get("Min Int Amt (Fund Ccy)", 0.0))
    sub_amt=float(staff_row.get("Min Sub Amt (Fund Ccy)", 0.0))

    if matched_pdf:
        house_status=f"🟢 MATCH ({staff_house})"
    else:
        house_status="🔴 NO MATCHING PDF"
    
    if staff_amt == engine_data["Min Int Amt"]:
        amt_status = "🟢 MATCH"
    else:
        amt_status = f"🔴 FAIL (Excel: {staff_amt} | PDF: {engine_data['Min Int Amt']})"

    if sub_amt == engine_data["Min Sub Amt"]:
        sub_status = "🟢 MATCH"
    else:
        sub_status = f"🔴 FAIL (Excel: {sub_amt} | PDF: {engine_data['Min Sub Amt']})"


    return {
        "Matched PDF": matched_pdf,
        "Management Company": house_status,
        "Target Fund": fund_name_target,
        "Currency": currency_target,
        "Min Int Amt Check": amt_status,
        "Min Sub Amt Check": sub_status
    }


def generate_audit_report(results_list, output_path):
    
    report_df = pd.DataFrame(results_list)
    
    report_df.to_excel(output_path, index=False)
    
    print("\n✅ SUCCESS! Target metrics computed. Report saved to: QA_Audit_Report.xlsx\n")




if __name__ == "__main__":
    company_data = load_target_data("mock_company_data.xlsx")
    
    final_audit_results = []

    for index, row in enumerate(company_data):
        fund_raw = str(row.get("Fund Name", ""))
        company_raw = str(row.get("Fund House", ""))
        ccy_target = str(row.get("Fund Currency", "")).lower()
        
        print(f"⚙️ Running Row {index + 1} Target: {fund_raw} ({ccy_target.upper()})")
        
        try:
            filename_used = find_and_lock_pdf(company_raw, fund_raw)
            
            extracted_metrics = extract_pdf_metrics(filename_used, ccy_target, fund_raw)
            
            audit_result = run_audit_comparison(row, extracted_metrics, filename_used)
            
            final_audit_results.append(audit_result)
            
        except Exception as row_error:
            
            print(f"❌ Error isolated on Row {index + 1}: {row_error}")
            final_audit_results.append({
                "Matched PDF": "EXCEPTION CAUGHT",
                "Management Company": "💥 SCRAPER EXCEPTION",
                "Target Fund": fund_raw,
                "Currency": ccy_target.upper(),
                "Min Int Amt Check": "💥 SCRAPER EXCEPTION",
                "Min Sub Amt Check": "💥 SCRAPER EXCEPTION"
            })

    
    
    
    generate_audit_report(final_audit_results)




            



            



