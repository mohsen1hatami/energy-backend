"""
وارد‌کننده داده کیفیت توان از خروجی واقعی کنتور هوشمند (فرمت DLMS/COSEM با
کدهای OBIS، دقیقاً همان ستون‌بندی که در نمونه data.csv شما دیده شد).

⚠️ هشدار مهم: این ماژول روی «ساختار ستون‌ها»ی data.csv نوشته و تست شده،
اما چون آن فایل هیچ ردیف داده‌ای نداشت (فقط هدر ۱۲۲ ستونی)، منطق تبدیل مقادیر
(خصوصاً نرمال‌سازی ضریب توان و ولتاژ pu) روی «داده واقعی» validate نشده.
همین که یک نمونه با چند ردیف واقعی بدهید، این فرضیات را می‌سنجیم و اصلاح
می‌کنیم:
  1) آیا «ضریب توان (Percent)» به‌صورت ۰ تا ۱۰۰ ذخیره شده یا ۰ تا ۱؟
  2) ولتاژ نامی مبنا برای محاسبه voltage_pu چند ولت است (تک‌فاز ۲۲۰/۲۳۰ یا
     سه‌فاز خط‌به‌خط ۳۸۰/۴۰۰)؟ این به نوع اشتراک (تک‌فاز/سه‌فاز) بستگی دارد.
"""
import re
import pandas as pd
import numpy as np

# نگاشت برچسب تمیزشده (بدون پسوند OBIS) -> نام داخلی استاندارد
COLUMN_MAP = {
    "ساعت/تاریخ (میلادی)": "timestamp_gregorian",
    "ساعت/تاریخ (شمسی)": "timestamp_jalali",
    "شماره پرونده/اشتراک": "subscriber_file_number",
    "شناسه قبض": "bill_id",
    "تعرفه": "tariff_code",
    "قدرت قراردادی": "contract_demand_kw",
    "نام": "first_name",
    "نام خانوادگی": "last_name",
    "نوع سیم بندی": "wiring_type",
    "دیماند رجیستر 5-مجموع قدرمطلق اکتیو انرژی مثبت و منفی (kW)": "registered_demand_kw",
    "میانگین ولتاژ فاز 1 (V)": "v1", "میانگین ولتاژ فاز 2 (V)": "v2", "میانگین ولتاژ فاز 3 (V)": "v3",
    "میانگین جریان فاز 1 (A)": "i1", "میانگین جریان فاز 2 (A)": "i2", "میانگین جریان فاز 3 (A)": "i3",
    "میانگین ضریب توان فاز 1 (Percent)": "pf1", "میانگین ضریب توان فاز 2 (Percent)": "pf2",
    "میانگین ضریب توان فاز 3 (Percent)": "pf3",
    "میانگین ضریب توان فاز کل (Percent)": "pf_total",
    "میانگین توان اکتیو مثبت (kw)": "active_power_import_kw",
    "میانگین توان اکتیو منفی (kW)": "active_power_export_kw",
    "میانگین توان راکتیو مثبت (kvar)": "reactive_power_import_kvar",
    "میانگین توان راکتیو منفی (kvar)": "reactive_power_export_kvar",
    "میانگین THD ولتاژ فاز1 (Percent)": "thd_v1", "میانگین THD ولتاژ فاز2 (Percent)": "thd_v2",
    "میانگین THD ولتاژ فاز3 (Percent)": "thd_v3",
    "میانگین THD جریان فاز1 (Percent)": "thd_i1", "میانگین THD جریان فاز2 (Percent)": "thd_i2",
    "میانگین THD جریان فاز3 (Percent)": "thd_i3",
    "اکتیو انرژی مثبت دوره ای(kWh)": "interval_active_energy_import_kwh",
    "اکتیو انرژی منفی دوره ای(kWh)": "interval_active_energy_export_kwh",
}

HARMONIC_ORDERS = [3, 5, 7, 9, 11, 13]
HARMONIC_FA = {3: "سوم", 5: "پنجم", 7: "هفتم", 9: "نهم", 11: "یازدهم", 13: "سیزدهم"}


def _strip_obis(col: str) -> str:
    """حذف پسوند کد OBIS از انتهای نام ستون، مثلا
    'میانگین ولتاژ فاز 1 (V)3#1.0.32.25.0.255#2' -> 'میانگین ولتاژ فاز 1 (V)'"""
    return re.sub(r"\d+#[\d.]+#\d+\s*$", "", col).strip()


def build_column_lookup(df_columns) -> dict:
    """دیکشنری {برچسب‌تمیز: نام‌ستون‌اصلی} برای دسترسی پایدار به ستون‌ها
    صرف‌نظر از دقیق‌بودن پسوند OBIS در نسخه‌های مختلف فایل خروجی."""
    return {_strip_obis(c): c for c in df_columns}


def _harmonic_columns(lookup: dict, kind: str) -> dict:
    """kind: 'ولتاژ' یا 'جریان'. خروجی: {phase: {order: colname}}"""
    out = {1: {}, 2: {}, 3: {}}
    for order, fa in HARMONIC_FA.items():
        for phase in (1, 2, 3):
            label = f"میانگین هارمونیک {fa} {kind} فاز {phase} (Percent)"
            if label in lookup:
                out[phase][order] = lookup[label]
    return out


def normalize_power_factor(raw_series: pd.Series) -> pd.Series:
    """اگر مقدار بیشینه ستون به‌وضوح >1.5 باشد، یعنی به‌صورت درصد (۰-۱۰۰)
    ذخیره شده و باید بر ۱۰۰ تقسیم شود. اگر داده هنوز نیامده (خالی)، فرض
    درصدی را به‌عنوان پیش‌فرض محافظه‌کارانه اعمال می‌کنیم — با ورود داده واقعی
    این فرض باید بازبینی شود."""
    s = pd.to_numeric(raw_series, errors="coerce")
    if s.notna().any() and s.abs().max() > 1.5:
        return s / 100.0
    return s


def import_pq_csv(csv_path: str, nominal_phase_voltage: float = 230.0) -> pd.DataFrame:
    """می‌خواند و به دیتافریم نرمال‌شده‌ی سازگار با ml_models.py/tariff_engine.py
    تبدیل می‌کند. هم CSV و هم اکسل (xlsx/xls) پشتیبانی می‌شود. اگر فایل ردیف
    داده نداشته باشد، دیتافریم خالی با ستون‌های درست برمی‌گرداند (برای این‌که
    بقیه پایپ‌لاین کرش نکند)."""
    if csv_path.lower().endswith((".xlsx", ".xls")):
        raw = pd.read_excel(csv_path, engine="openpyxl" if csv_path.lower().endswith(".xlsx") else None)
    else:
        raw = pd.read_csv(csv_path, encoding="utf-8-sig")
    lookup = build_column_lookup(raw.columns)

    out = pd.DataFrame(index=raw.index)
    for fa_label, internal_name in COLUMN_MAP.items():
        col = lookup.get(fa_label)
        out[internal_name] = raw[col] if col is not None else np.nan

    if len(raw) == 0:
        out["timestamp"] = pd.Series(dtype="datetime64[ns]")
    else:
        out["timestamp"] = pd.to_datetime(out["timestamp_gregorian"], errors="coerce")

    out["active_power_kw"] = pd.to_numeric(out["active_power_import_kw"], errors="coerce")

    out["power_factor"] = normalize_power_factor(out["pf_total"])

    for ph in (1, 2, 3):
        out[f"v{ph}"] = pd.to_numeric(out[f"v{ph}"], errors="coerce")
    out["voltage_pu"] = out[["v1", "v2", "v3"]].mean(axis=1) / nominal_phase_voltage
    v_max = out[["v1", "v2", "v3"]].max(axis=1)
    v_min = out[["v1", "v2", "v3"]].min(axis=1)
    v_avg = out[["v1", "v2", "v3"]].mean(axis=1)
    out["voltage_imbalance_percent"] = (v_max - v_min) / v_avg.replace(0, np.nan) * 100

    out["thd_percent"] = out[["thd_v1", "thd_v2", "thd_v3"]].astype(float).mean(axis=1)

    harmonic_v_cols = _harmonic_columns(lookup, "ولتاژ")
    harmonic_i_cols = _harmonic_columns(lookup, "جریان")
    for order in HARMONIC_ORDERS:
        v_cols = [harmonic_v_cols[p][order] for p in (1, 2, 3) if order in harmonic_v_cols[p]]
        i_cols = [harmonic_i_cols[p][order] for p in (1, 2, 3) if order in harmonic_i_cols[p]]
        out[f"harmonic_v_h{order}_percent"] = raw[v_cols].astype(float).mean(axis=1) if v_cols else np.nan
        out[f"harmonic_i_h{order}_percent"] = raw[i_cols].astype(float).mean(axis=1) if i_cols else np.nan

    out["contract_demand_kw"] = pd.to_numeric(out["contract_demand_kw"], errors="coerce")
    out["registered_demand_kw"] = pd.to_numeric(out["registered_demand_kw"], errors="coerce")

    keep = [
        "timestamp", "subscriber_file_number", "tariff_code", "contract_demand_kw",
        "active_power_kw", "power_factor", "voltage_pu", "voltage_imbalance_percent",
        "thd_percent", "registered_demand_kw",
        "v1", "v2", "v3",
    ] + [f"harmonic_v_h{o}_percent" for o in HARMONIC_ORDERS] + [
        f"harmonic_i_h{o}_percent" for o in HARMONIC_ORDERS
    ]
    return out[keep]


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/data.csv"
    df = import_pq_csv(path)
    print(f"ردیف‌های خوانده‌شده: {len(df)}")
    print(df.dtypes)
    if len(df) == 0:
        print("\n⚠️ فایل ورودی فقط هدر دارد — هیچ ردیف داده‌ای برای تست مقادیر واقعی وجود ندارد.")
