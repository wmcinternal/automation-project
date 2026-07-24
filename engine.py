import os
import re
import time
import random
import urllib.request
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import pandas as pd
from rapidfuzz import fuzz
import pdfplumber

STOP_PHRASES=["not offered", "not available", "no longer offered", "n/a", "none", "nil", "not permitted"]
    

print("\n🚀 Booting up Solid Backend MVP (Read -> Compare -> Write Mode)...\n")

def webscrap_sfc_pdf(ce_number, folder="./webscrap"):

    if not ce_number or pd.isna(ce_number):
        return None
    
    os.makedirs(folder, exist_ok=True)

    ce_clean=str(ce_number).strip().upper()
    ce_name=f"{ce_clean}_KPS.pdf"
    ce_path=os.path.join(folder, ce_name)

    if os.path.exists(ce_path):
        print(f"   ⏭️  [Cache Hit] {ce_name} already exists in {folder}.")
        return ce_path

    url_path=f"https://apps.sfc.hk/productlistWeb/searchProduct/getDocListNoDate.do?lang=EN&ceref={ce_clean}&docType=OD"
    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    try:
        print(f"   🌐 Connecting to SFC index for CE: {ce_clean}...")
        
        time.sleep(random.uniform(1.0, 2.0))
        
        req=urllib.request.Request(url_path, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as response:
            html_content=response.read()
        
        soup=BeautifulSoup(html_content, 'html.parser')
        matching_links = []
        for link in soup.find_all('a', href=True):

            onclick_text=link.get('onclick', '')

            if 'getDoc.do' in onclick_text:
                match=re.search(r"window\.location\.href='([^']+)'", onclick_text)

                if match:
                    matching_links.append(match.group(1))
                    


        if len(matching_links) > 0:
            bottom_href=matching_links[-1]

            full_url=urljoin(url_path, bottom_href)

            print(f"   📥 Bottom Link Found! URL: {full_url}")

            time.sleep(random.uniform(1.5, 3.5))

            target_req=urllib.request.Request(full_url, headers=headers)
            with urllib.request.urlopen(target_req, timeout=12) as stream:
                with open(ce_path, 'wb') as out_file:
                    out_file.write(stream.read())

            print(f"   ✅ Saved completely to: {ce_path}")
            return ce_path
    
    except Exception as e:
        
        print(f"   ❌ Network error for CE {ce_clean}: {e}")
        return None





def load_target_data(excel_path):
    try:
        print(f"📂 Loading target instructions from {excel_path}...")
        df=pd.read_excel(excel_path)
        return df.to_dict('records')
    except FileNotFoundError:
        print(f"❌ CRITICAL ERROR: Could not find '{excel_path}' in this folder!")
        exit()


def extract_dealing_frequency(pdf):
                
    for page in pdf.pages:
        page_text=page.extract_text() or ""
        if page_text:
            freq_match = re.search(
                r"(?:dealing\s*frequency|dealing\s*day)s?(?:[/\s|]*dealing\s*day)?[\s:|]*([^\n\r.]+)",
                page_text, 
                re.IGNORECASE
            )
            if freq_match:
                raw_val = freq_match.group(1).strip()
                clean_freq = re.sub(r'[¹²³⁴⁵⁶⁷⁸⁹⁰#*†‡^]', '', raw_val).strip()
                clean_freq = clean_freq.strip(':| ').strip()
                if clean_freq:
                    return clean_freq

    return "NOT FOUND"


def extract_borderless_window_amount(pdf, search_currencies, share_class_target):
    
    ccy_pattern = "|".join(search_currencies)
    
    for page in pdf.pages:
        page_text = page.extract_text() or ""
        if not page_text:
            continue

        lines = [line.strip() for line in page_text.split('\n') if line.strip()]

        for idx, line in enumerate(lines):
            line_lower = line.lower()

            if any(k in line_lower for k in ["min. investment", "minimum investment", "minimum subscription", "initial investment"]):
        
                window_lines = lines[idx : min(idx + 12, len(lines))]
                window_text = "\n".join(window_lines)

                class_match = re.search(
                    rf"\bclass\s*{share_class_target}\b|\b{share_class_target}\b", 
                    window_text, 
                    re.IGNORECASE
                )

                if class_match:
                    if any(sp in window_text.lower() for sp in STOP_PHRASES):
                        return 0.0, 0.0

                    amounts = re.findall(rf"(?:{ccy_pattern}|\$)\s*([\d,]+(?:\.\d+)?)", window_text, re.IGNORECASE)

                    if amounts:
                        clean_nums = [float(a.replace(',', '')) for a in amounts]
                        v_init = clean_nums[0]
                        v_sub = clean_nums[1] if len(clean_nums) > 1 else clean_nums[0]
                        return v_init, v_sub

    return 0.0, 0.0

def parse_amount(text, search_candidates):

    cleaned_text = text.lower().strip()
    if any(phrase in cleaned_text for phrase in STOP_PHRASES):
        return 0.0
    
    active_ccy = None
    for ccy in search_candidates:
        if ccy in cleaned_text:
            active_ccy = ccy
            break
    
    if not active_ccy and "usd" in cleaned_text:
        active_ccy = "usd"
        
    if not active_ccy:
        return 0.0
        
    match = re.search(rf"{active_ccy}[ \t]*([\d,.]+)[ \t]*(million|m)?", cleaned_text)
    if match:
        val = float(match.group(1).replace(',', ''))
        if match.group(2): 
            val *= 1000000 
        return val
    return 0.0


def extract_subscription_amount(pdf, search_currencies, share_class_target):

    class_pattern = rf"\bclass(?:es)?\b.*?\b{share_class_target}\b"
    section_break_keywords = [
        "ongoing charge", "what is this product", "quick facts", 
        "objectives and investment", "dividend policy", "management company"
    ]

    for page in pdf.pages:    
        tables = page.extract_tables() or []
                
        for table in tables:
            if not table or len(table[0]) < 2:
                continue
                        
            in_investment_section = False
            col_idx_initial = 1  
            col_idx_additional = 2
                    
            in_target_class_block = False
            block_initial_text = ""
            block_additional_text = ""

            for row_idx, row in enumerate(table):        
                clean_row = [str(cell).lower().replace('\n', ' ').strip() if cell else "" for cell in row]
                if in_investment_section:
                    row_text_combined = " ".join(clean_row)
                    if any(k in row_text_combined for k in section_break_keywords):
                        in_investment_section = False
                        break

                is_section_header = any(
                    k in cell for cell in clean_row for k in ["min. invest", "minimum invest", "min. investment", "minimum subcription amount", "minimum subsequent subscription amount", "minimum investment"]
                )
                        
                if is_section_header:
                    in_investment_section = True
                            
                    for i, cell in enumerate(clean_row):
                        if "initial" in cell or "minimum subscription amount" in cell:
                            col_idx_initial = i

                        elif "additional" in cell or "subsequent" in cell:
                            col_idx_additional = i
                                    
                    if row_idx + 1 < len(table):
                        next_row = [str(cell).lower().replace('\n', ' ').strip() if cell else "" for cell in table[row_idx + 1]]
                        for i, cell in enumerate(next_row):
                            if "initial" in cell or "minimum subsequent subcription amount" in cell:
                                col_idx_initial = i
                            elif "additional" in cell or "subsequent" in cell:
                                col_idx_additional = i
                    continue

                if in_investment_section:
                    cell_0 = clean_row[0] if len(clean_row) > 0 else ""
                    cell_initial = clean_row[col_idx_initial] if len(clean_row) > col_idx_initial else ""
                    cell_additional = clean_row[col_idx_additional] if len(clean_row) > col_idx_additional else ""
                    
                    if cell_0.strip() and not cell_initial.strip() and not cell_additional.strip():
                        continue

                    if cell_0.strip():
                        if in_target_class_block:
                            break

                        if re.search(class_pattern, cell_0):
                            in_target_class_block = True
                            block_initial_text = cell_initial
                            block_additional_text = cell_additional
                    else:
                        if in_target_class_block:
                            if cell_initial.strip():
                                block_initial_text += " " + cell_initial
                            if cell_additional.strip():
                                block_additional_text += " " + cell_additional

            if in_target_class_block:
                min_int=parse_amount(block_initial_text, search_currencies)
                min_sub=parse_amount(block_additional_text, search_currencies)
            
                if min_int > 0 or min_sub > 0:
                    return min_int, min_sub


    ccy_pattern = "|".join(search_currencies)
    for page in pdf.pages:
        page_text = page.extract_text() or ""
        if not page_text:
            continue
            
        lines = page_text.split('\n')
        in_text_section=False

        for line in lines:
            line_lower = line.lower().strip()

            if any(k in line_lower for k in ["min. investment", "minimum investment", "minimum subscription", "min. sub"]):
                in_text_section = True
                continue

            if in_text_section:
                class_match = re.search(rf"\bclass\s*{share_class_target}\b|\bclass\b.*?\b{share_class_target}\b|\b{share_class_target}\b", line_lower)
                if class_match:
                        
                    if any(phrase in line_lower for phrase in STOP_PHRASES):
                        return 0.0, 0.0

                    amounts = re.findall(rf"(?:{ccy_pattern})\s*[\d,.]+", line_lower, re.IGNORECASE)
                    if amounts:
                        def clean_val(amt_str):
                            m = re.search(r"[\d,.]+", amt_str)
                            return float(m.group(0).replace(',', '')) if m else 0.0
                            
                        min_int=clean_val(amounts[0])
                        min_sub=clean_val(amounts[1]) if len(amounts) > 1 else clean_val(amounts[0])
                        return min_int, min_sub
    
    return 0.0, 0.0


def extract_pdf_metrics(matched_pdf, target_currency, target_fund, folder="."):
    engine_data = {
        "Company": "NOT FOUND", 
        "Min Int Amt": 0.0, 
        "Min Sub Amt": 0.0, 
        "Dealing Freq": "NOT FOUND"
    }

    if not matched_pdf:
        return engine_data


    target_lower = target_currency.lower().strip()
    if target_lower in ["cny", "rmb", "cnh"]:
        search_currencies = ["rmb", "cnh", "cny"]
    else:
        search_currencies = [target_lower]

    clean_fund_name = re.sub(r'[^a-zA-Z0-9 ]', ' ', target_fund).lower()
    tokens = clean_fund_name.split()
    tokens.reverse()
    
    share_class_target = "a"  
    class_prefixes = ('a', 'b', 'c', 'i', 'w', 's')
    distribution_suffixes = ["acc", "dis", "inc", "dec", "mdist", "hedged", "h", "hdg"]

    for token in tokens:
        if token in ["usd", "eur", "gbp", "aud", "nzd", "hkd", "sgd", "cad", "rmb", "cny"] or token in distribution_suffixes:
            continue
        if token.startswith(class_prefixes) and len(token) <= 3:
            share_class_target = token
            break


    try:
        with pdfplumber.open(os.path.join(folder, matched_pdf)) as pdf:

            engine_data["Dealing Freq"]=extract_dealing_frequency(pdf);
            min_int, min_sub=extract_subscription_amount(pdf, search_currencies, share_class_target )

            if min_int==0.0 and min_sub==0.0:
                min_int, min_sub=extract_borderless_window_amount(pdf, search_currencies, share_class_target);
            
            engine_data["Min Int Amt"] = min_int
            engine_data["Min Sub Amt"] = min_sub

    except Exception as e:
        print(f"⚠️ Error scraping table details from {matched_pdf}: {e}")


    return engine_data




def run_audit_comparison(staff_row, engine_data, matched_pdf):

    ce_number = staff_row.get("SFC Sub-fund CE No.")
    staff_house = str(staff_row.get("Fund House", ""))
    fund_name_target = str(staff_row.get("Fund Name", ""))
    currency_target = str(staff_row.get("Fund Currency", "")).upper()
    staff_amt = float(staff_row.get("Min Int Amt (Fund Ccy)", 0.0))
    sub_amt=float(staff_row.get("Min Sub Amt (Fund Ccy)", 0.0))
    
    raw_freq = staff_row.get("Dealing Freq.", staff_row.get("Dealing Freq", staff_row.get("Dealing Frequency", "")))
    if pd.isna(raw_freq) or str(raw_freq).strip().lower() in ["nan", "none", ""]:
        staff_freq = ""
    else:
        staff_freq = str(raw_freq).strip().lower()


    if pd.isna(ce_number) or str(ce_number).strip()=="":
        return {
            "Matched PDF": "⚪ BLANK ROW",
            "Management Company": "⚪ BLANK ROW",
            "Target Fund": fund_name_target,
            "Currency": currency_target.upper(),
            "Min Int Amt Check": "⚪ BLANK ROW",
            "Min Sub Amt Check": "⚪ BLANK ROW",
            "Dealing Freq Check": "⚪ BLANK ROW"
        }


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


    pdf_freq = str(engine_data.get("Dealing Freq", "NOT FOUND")).strip().lower()
    if staff_freq and pdf_freq!= "not found" and staff_freq in pdf_freq:
        freq_status = "🟢 MATCH"
    else:
        freq_status = f"🔴 FAIL (Excel: {staff_freq if staff_freq else 'N/A'} | PDF: {pdf_freq})"

    return {
        "Matched PDF": matched_pdf,
        "Management Company": house_status,
        "Target Fund": fund_name_target,
        "Currency": currency_target,
        "Min Int Amt Check": amt_status,
        "Min Sub Amt Check": sub_status,
        "Dealing Freq Check": freq_status

    }


def generate_audit_report(results_list, output_path):
    
    report_df = pd.DataFrame(results_list)
    
    report_df.to_excel(output_path, index=False)
    
    print("\n✅ SUCCESS! Target metrics computed. Report saved to: QA_Audit_Report.xlsx\n")




if __name__ == "__main__":
    company_data = load_target_data("template_golden.xlsx")
    
    final_audit_results = []

    BAS_DIR=os.path.dirname(os.path.abspath(__file__))
    WEBSCRAP_FOLDER=os.path.join(BAS_DIR, "webscrap")

    for index, row in enumerate(company_data):
        fund_raw = str(row.get("Fund Name", ""))
        company_raw = str(row.get("Fund House", ""))
        ccy_target = str(row.get("Fund Currency", "")).lower()
        
        
        ce_number=row.get("SFC Sub-fund CE No.")
        print(f"⚙️ Running Row {index + 1} Target: {fund_raw} ({ccy_target.upper()})")


        try:

            webscrap_sfc_pdf(ce_number, folder=WEBSCRAP_FOLDER)

            ce_clean=str(ce_number).strip().upper()
            filename_used=f"{ce_clean}_KPS.pdf"

            ce_file=os.path.join(WEBSCRAP_FOLDER, filename_used)

            if not os.path.exists(ce_file):
                filename_used=None

            extracted_metrics = extract_pdf_metrics(filename_used, ccy_target, fund_raw, folder=WEBSCRAP_FOLDER)
            
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
                "Min Sub Amt Check": "💥 SCRAPER EXCEPTION", 
                "Dealing Freq Check": "💥 SCRAPER EXCEPTION"
            })

    
    
    
    generate_audit_report(final_audit_results, 'QA_Audit_Report.xlsx')




            



            



