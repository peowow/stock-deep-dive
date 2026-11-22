import json
import math
import os
import re
from datetime import datetime, timezone

import requests
import yfinance as yf
from bs4 import BeautifulSoup

OUTPUT_PATH = os.path.join("data", "data.json")
TICKERS_FILE = "tickers.txt"

DAMODARAN_URLS = {
    "pe": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pedata.html",
    "margin": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/margin.html",
    "roe": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/roe.html",
    "pbv": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pbvdata.html",
    "debt": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/dbtfund.html",
}

YAHOO_TO_DAMODARAN = {
    "consumer electronics": "Computers/Peripherals",
    "software - infrastructure": "Software (System & Application)",
    "software - application": "Software (System & Application)",
    "internet content & information": "Software (Internet)",
    "semiconductors": "Semiconductor",
    "semiconductor equipment & materials": "Semiconductor Equip",
    "internet retail": "Retail (Online)",
    "discount stores": "Retail (General)",
    "home improvement retail": "Retail (Building Supply)",
    "specialty retail": "Retail (Special Lines)",
    "auto manufacturers": "Auto & Truck",
    "banks - diversified": "Bank (Money Center)",
    "banks - regional": "Banks (Regional)",
    "credit services": "Financial Svcs. (Non-bank & Insurance)",
    "asset management": "Investments & Asset Management",
    "capital markets": "Brokerage & Investment Banking",
    "drug manufacturers - general": "Drugs (Pharmaceutical)",
    "biotechnology": "Drugs (Biotechnology)",
    "medical devices": "Healthcare Products",
    "healthcare plans": "Healthcare Support Services",
    "oil & gas integrated": "Oil/Gas (Integrated)",
    "oil & gas e&p": "Oil/Gas (Production and Exploration)",
    "aerospace & defense": "Aerospace/Defense",
    "telecom services": "Telecom. Services",
    "communication equipment": "Telecom. Equipment",
    "entertainment": "Entertainment",
    "restaurants": "Restaurant/Dining",
    "beverages - non-alcoholic": "Beverage (Soft)",
    "household & personal products": "Household Products",
    "packaged foods": "Food Processing",
    "utilities - regulated electric": "Utility (General)",
    "reit - specialty": "R.E.I.T.",
    "railroads": "Transportation (Railroads)",
    "integrated freight & logistics": "Air Transport",
    "airlines": "Air Transport",
}


def clean_num(val):
    if val is None:
        return None
    try:
        if isinstance(val, bool):
            return None
        f = float(val)
        return round(f, 6) if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def parse_stern_cell(text):
    t = text.strip().replace(",", "").replace("$", "")
    if not t or t.upper() in {"NA", "N/A", "NM", "-", "--"}:
        return None
    t = t.replace("%", "")
    try:
        val = float(t)
        return round(val, 4) if math.isfinite(val) else None
    except ValueError:
        return None


def fetch_damodaran_table(url):
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    target_table = None
    header_idx = 0
    headers = []

    for tbl in soup.find_all("table"):
        rows = tbl.find_all("tr")
        for idx, tr in enumerate(rows[:5]):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if any("industry name" in c.lower() for c in cells):
                target_table = rows
                header_idx = idx
                headers = cells
                break
        if target_table:
            break

    if not target_table:
        return {}

    col_idx = next((i for i, h in enumerate(headers) if "industry name" in h.lower()), 0)
    data = {}
    for tr in target_table[header_idx + 1 :]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) <= col_idx:
            continue
        ind_name = re.sub(r"\s+", " ", cells[col_idx]).strip()
        if not ind_name or ind_name.lower().startswith("total"):
            continue
        row_dict = {}
        for i, h in enumerate(headers):
            if i == col_idx or i >= len(cells):
                continue
            row_dict[h.strip()] = parse_stern_cell(cells[i])
        data[ind_name] = row_dict
    return data


def find_header_value(row, keywords):
    for k, v in row.items():
        lk = k.lower()
        if any(kw in lk for kw in keywords) and v is not None:
            return v
    return None


def build_damodaran_benchmarks():
    merged = {}
    try:
        pe_tbl = fetch_damodaran_table(DAMODARAN_URLS["pe"])
        margin_tbl = fetch_damodaran_table(DAMODARAN_URLS["margin"])
        roe_tbl = fetch_damodaran_table(DAMODARAN_URLS["roe"])
        pbv_tbl = fetch_damodaran_table(DAMODARAN_URLS["pbv"])
        debt_tbl = fetch_damodaran_table(DAMODARAN_URLS["debt"])

        all_inds = set(pe_tbl) | set(margin_tbl) | set(roe_tbl) | set(pbv_tbl) | set(debt_tbl)
        for ind in all_inds:
            pe_r = pe_tbl.get(ind, {})
            mg_r = margin_tbl.get(ind, {})
            ro_r = roe_tbl.get(ind, {})
            pb_r = pbv_tbl.get(ind, {})
            db_r = debt_tbl.get(ind, {})

            merged[ind] = {
                "trailingPE": find_header_value(pe_r, ["trailing pe"]),
                "forwardPE": find_header_value(pe_r, ["forward pe"]),
                "priceToBook": find_header_value(pb_r, ["pbv", "price/bv", "price to book"]),
                "operatingMargin": find_header_value(mg_r, ["pre-tax operating margin", "operating margin"]),
                "netMargin": find_header_value(mg_r, ["net margin"]),
                "roe": find_header_value(ro_r, ["roe"]),
                "roic": find_header_value(ro_r, ["roic", "roc"]),
                "debtToEquity": find_header_value(db_r, ["d/e", "debt/equity", "debt to equity"]),
            }
    except Exception as exc:
        print(f"Warning: Damodaran fetch partial/failed: {exc}")

    if not merged and os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
                old = json.load(f)
                merged = old.get("_industryBenchmark", {})
        except Exception:
            pass
    return merged


def match_industry(yahoo_ind, benchmarks):
    if not yahoo_ind or not benchmarks:
        return None, None
    key = yahoo_ind.strip().lower()
    if key in YAHOO_TO_DAMODARAN:
        mapped = YAHOO_TO_DAMODARAN[key]
        if mapped in benchmarks:
            return mapped, benchmarks[mapped]

    for b_name in benchmarks:
        if b_name.lower() == key:
            return b_name, benchmarks[b_name]

    y_words = set(re.findall(r"[a-z]+", key)) - {"and", "the", "of", "services", "general"}
    best_name, best_score = None, 0
    for b_name in benchmarks:
        b_words = set(re.findall(r"[a-z]+", b_name.lower())) - {"and", "the", "of", "services", "general"}
        score = len(y_words & b_words)
        if score > best_score:
            best_score = score
            best_name = b_name
    if best_name and best_score > 0:
        return best_name, benchmarks[best_name]
    return None, None


def get_row_val(df, col, candidates):
    if df is None or df.empty or col not in df.columns:
        return None
    for name in candidates:
        if name in df.index:
            val = clean_num(df.loc[name, col])
            if val is not None:
                return val
    return None


def extract_annuals(tk):
    inc = bal = cf = None
    try:
        inc = tk.income_stmt
    except Exception:
        pass
    try:
        bal = tk.balance_sheet
    except Exception:
        pass
    try:
        cf = tk.cashflow
    except Exception:
        pass

    cols = []
    if inc is not None and not inc.empty:
        cols = list(inc.columns)
    elif cf is not None and not cf.empty:
        cols = list(cf.columns)
    elif bal is not None and not bal.empty:
        cols = list(bal.columns)

    cols = sorted(cols, reverse=True)[:6]
    annuals = []

    for col in cols:
        date_str = str(col)[:10]
        year_str = date_str[:4]

        rev = get_row_val(inc, col, ["Total Revenue", "Operating Revenue"])
        ni = get_row_val(inc, col, ["Net Income", "Net Income Common Stockholders", "Net Income Continuous Operations"])
        eps = get_row_val(inc, col, ["Diluted EPS", "Basic EPS"])
        ebit = get_row_val(inc, col, ["EBIT", "Operating Income"])
        int_exp = get_row_val(inc, col, ["Interest Expense", "Interest Expense Non Operating"])
        tax_prov = get_row_val(inc, col, ["Tax Provision", "Income Tax Expense"])
        pretax = get_row_val(inc, col, ["Pretax Income"])

        fcf = get_row_val(cf, col, ["Free Cash Flow"])
        if fcf is None:
            ocf = get_row_val(cf, col, ["Operating Cash Flow", "Total Cash From Operating Activities"])
            capex = get_row_val(cf, col, ["Capital Expenditure", "Capital Expenditures"])
            if ocf is not None and capex is not None:
                fcf = clean_num(ocf - abs(capex))

        bs_col = col if (bal is not None and not bal.empty and col in bal.columns) else None
        if bs_col is None and bal is not None and not bal.empty:
            for bc in bal.columns:
                if str(bc)[:4] == year_str:
                    bs_col = bc
                    break

        equity = get_row_val(bal, bs_col, ["Stockholders Equity", "Common Stock Equity", "Total Stockholder Equity"])
        total_debt = get_row_val(bal, bs_col, ["Total Debt"])
        cash = get_row_val(bal, bs_col, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"])

        annuals.append({
            "date": date_str,
            "year": year_str,
            "revenue": rev,
            "netIncome": ni,
            "eps": eps,
            "fcf": fcf,
            "ebit": ebit,
            "interestExpense": int_exp,
            "taxProvision": tax_prov,
            "pretaxIncome": pretax,
            "equity": equity,
            "totalDebt": total_debt,
            "cash": cash,
        })

    annuals.reverse()
    return annuals


def safe_df_cell(df, row_key, col_key):
    try:
        if df is not None and not df.empty and row_key in df.index and col_key in df.columns:
            return clean_num(df.loc[row_key, col_key])
    except Exception:
        pass
    return None


def extract_estimates(tk):
    result = {
        "earningsHistory": [],
        "fwdRevenueAvg": None,
        "fwdRevenueGrowth": None,
        "fwdEpsAvg": None,
        "fwdEpsGrowth": None,
        "curYearRevenueAvg": None,
        "curYearEpsAvg": None,
    }

    try:
        eh = tk.earnings_history
        if eh is not None and not eh.empty:
            for idx, row in eh.tail(4).iterrows():
                q_date = str(idx)[:10]
                actual = clean_num(row.get("epsActual"))
                est = clean_num(row.get("epsEstimate"))
                surp = clean_num(row.get("surprisePercent"))
                if surp is not None and abs(surp) <= 2.0:
                    surp = round(surp * 100, 2)
                result["earningsHistory"].append({
                    "quarter": q_date,
                    "epsEstimate": est,
                    "epsActual": actual,
                    "surprisePct": surp,
                })
            result["earningsHistory"].sort(key=lambda x: x["quarter"])
    except Exception:
        pass

    try:
        re_df = tk.revenue_estimate
        result["fwdRevenueAvg"] = safe_df_cell(re_df, "+1y", "avg")
        result["fwdRevenueGrowth"] = safe_df_cell(re_df, "+1y", "growth")
        result["curYearRevenueAvg"] = safe_df_cell(re_df, "0y", "avg")
    except Exception:
        pass

    try:
        ee_df = tk.earnings_estimate
        result["fwdEpsAvg"] = safe_df_cell(ee_df, "+1y", "avg")
        result["fwdEpsGrowth"] = safe_df_cell(ee_df, "+1y", "growth")
        result["curYearEpsAvg"] = safe_df_cell(ee_df, "0y", "avg")
    except Exception:
        pass

    return result


def read_tickers():
    if not os.path.exists(TICKERS_FILE):
        return []
    syms = []
    with open(TICKERS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip().upper()
            if line and line not in syms:
                syms.append(line)
    return syms


def main():
    symbols = read_tickers()
    benchmarks = build_damodaran_benchmarks()
    tickers_out = {}
    errors = {}

    for sym in symbols:
        print(f"Fetching {sym}...")
        try:
            tk = yf.Ticker(sym)
            info = tk.info or {}

            price = clean_num(info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose"))
            div_rate = clean_num(info.get("dividendRate"))
            div_yield = None
            if div_rate is not None and price and price > 0:
                div_yield = round(div_rate / price, 6)
            else:
                raw_dy = clean_num(info.get("dividendYield"))
                if raw_dy is not None:
                    div_yield = round(raw_dy / 100.0 if raw_dy > 0.35 else raw_dy, 6)

            y_ind = info.get("industry")
            b_name, _ = match_industry(y_ind, benchmarks)
            annuals = extract_annuals(tk)
            estimates = extract_estimates(tk)

            tickers_out[sym] = {
                "symbol": sym,
                "shortName": info.get("shortName") or info.get("longName") or sym,
                "industry": y_ind,
                "sector": info.get("sector"),
                "benchmarkIndustry": b_name,
                "currentPrice": price,
                "marketCap": clean_num(info.get("marketCap")),
                "enterpriseValue": clean_num(info.get("enterpriseValue")),
                "trailingPE": clean_num(info.get("trailingPE")),
                "forwardPE": clean_num(info.get("forwardPE")),
                "priceToBook": clean_num(info.get("priceToBook")),
                "trailingEps": clean_num(info.get("trailingEps")),
                "forwardEps": clean_num(info.get("forwardEps")),
                "sharesOutstanding": clean_num(info.get("sharesOutstanding")),
                "totalRevenue": clean_num(info.get("totalRevenue")),
                "netIncomeToCommon": clean_num(info.get("netIncomeToCommon")),
                "ebitda": clean_num(info.get("ebitda")),
                "freeCashflow": clean_num(info.get("freeCashflow")),
                "operatingMargins": clean_num(info.get("operatingMargins")),
                "profitMargins": clean_num(info.get("profitMargins")),
                "grossMargins": clean_num(info.get("grossMargins")),
                "returnOnEquity": clean_num(info.get("returnOnEquity")),
                "returnOnAssets": clean_num(info.get("returnOnAssets")),
                "totalCash": clean_num(info.get("totalCash")),
                "totalDebt": clean_num(info.get("totalDebt")),
                "debtToEquity": clean_num(info.get("debtToEquity")),
                "quickRatio": clean_num(info.get("quickRatio")),
                "bookValue": clean_num(info.get("bookValue")),
                "dividendRate": div_rate,
                "dividendYield": div_yield,
                "payoutRatio": clean_num(info.get("payoutRatio")),
                "annuals": annuals,
                "estimates": estimates,
            }
        except Exception as exc:
            errors[sym] = str(exc)
            print(f"Error fetching {sym}: {exc}")

    os.makedirs("data", exist_ok=True)
    payload = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "tickers": tickers_out,
        "errors": errors,
        "_industryBenchmark": benchmarks,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Saved {len(tickers_out)} tickers to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
