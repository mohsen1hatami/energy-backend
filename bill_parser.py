"""
پارسر قبض برق ایران (فرمت شرکت توزیع نیروی برق استان خراسان شمالی، تولیدشده
با Crystal Reports). کالیبره‌شده روی یک نمونه واقعی قبض «سایر مصارف».

نکته فنی مهم: در PDFهای فارسی ساخته‌شده با Crystal Reports، ابزار pdftotext
اغلب ترتیب visual متن دوطرفه (RTL/LTR) را به‌هم می‌ریزد؛ در نتیجه در بسیاری
از سطرها «مقدار» قبل از «برچسب» می‌آید (برعکس آنچه در PDF دیده می‌شود).
این پارسر برای هر فیلد، جهت واقعی مشاهده‌شده در متن استخراج‌شده را در نظر
گرفته است.

نکته دامنه‌ای مهم: این قبض از نوع «سایر مصارف» با تعرفه پلکانی + سورشارژ
اوج‌بار + تخفیف کم‌باری است — نه صنعتی/تجاری بزرگ با دیماند و ضریب قدرت.
اگر قبض صنعتی با دیماند داشتید، باید الگوهای مربوط به آن (که در این قبض
اصلاً وجود ندارند) را جداگانه اضافه کنیم.
"""
import re
import subprocess
import json


def _clean(text: str) -> str:
    return re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", text)


def _num(s):
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return None


def _search(pattern, text, group=1, flags=0):
    m = re.search(pattern, text, flags)
    return m.group(group).strip() if m else None


def extract_text(pdf_path: str) -> str:
    out = subprocess.run(
        ["pdftotext", "-layout", pdf_path, "-"], capture_output=True, text=True, check=True,
    )
    return _clean(out.stdout)


def parse_bill(pdf_path: str) -> dict:
    text = extract_text(pdf_path)
    result = {}

    result["subscriber_name"] = _search(r"مشترک\s*محترم\s*:\s*([^\n]+)", text)
    result["address"] = _search(r"نشانی\s*:\s*([^\n]+)", text)
    result["tariff_title"] = _search(r"عنوان\s*و\s*کد\s*تعرفه\s*:\s*([^\n]+)", text)

    result["postal_code"] = _search(r"کد\s*پستی\s*([\d]{6,10})\s*:", text)
    result["file_number"] = _search(r"(\d+)\s+پرونده\s*:", text)
    result["address_code"] = _search(r"([\d/]+)\s+کد\s*آدرس\s*:", text)
    result["meter_body_serial"] = _search(r"بدنه\s*کنتور\s*([\d]+)\s*:", text)
    result["computer_code"] = _search(r"رمز\s*رايانه\s*([\d]+)\s*:", text)
    result["economic_code"] = _search(r"([\d]+)\s+کد\s*اقتصادی\s*:", text)
    m = re.search(r"منطقه\s*برق\s*(\d+)\s*:\s*([^\n]+)", text)
    if m:
        result["region_code"], result["region_name"] = m.group(1), m.group(2).strip()

    result["days_covered"] = _num(_search(r"تعداد\s*روز\s*(\d+)\s*:", text))
    result["avg_consumption_30d_kwh"] = _num(_search(r"روزه\s*([\d.]+)\s*:", text))
    result["total_consumption_kwh"] = _num(_search(r"(\d[\d,]*)\s+مصرف\s*کل\s*دوره", text))
    result["issue_date_jalali"] = _search(r"صدور\s*صورتحساب\s*:?\s*([\d/]+)", text)

    band_names = ["low_load", "peak_load", "mid_load"]
    cons = re.search(r"([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+مصرف\s*kwh", text)
    result["consumption_by_band_kwh"] = (
        dict(zip(band_names, [_num(cons.group(i)) for i in (1, 2, 3)])) if cons else {}
    )

    cur = re.search(r"([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d/]+)\s+قرائت\s*کنونی", text)
    readings = {"current": {}, "previous": {}}
    if cur:
        vals = [_num(cur.group(i)) for i in (1, 2, 3)]
        readings["current"] = dict(zip(band_names, vals))
        readings["current"]["date_jalali"] = cur.group(4)
    prev = re.search(
        r"([\d,]+)\s*\n[^\n]*مبلغ\s*مصرف\s+\d+[^\n]*?([\d,]+)\s+([\d,]+)\s+قرائت\s*پیشین\s+([\d/]+)",
        text,
    )
    if prev:
        readings["previous"] = {
            "low_load": _num(prev.group(2)),
            "peak_load": _num(prev.group(1)),
            "mid_load": _num(prev.group(3)),
            "date_jalali": prev.group(4),
        }
    result["meter_readings"] = readings

    tiers = []
    for m in re.finditer(r"(\d{5,9})\s+(\d{2,4})\s+(\d{3,6})\s+([^\n]{0,22})", text):
        amount, qty, rate, tail = m.groups()
        bound_match = re.search(r"(\d+)\s*تا\s*(\d+)", tail)
        surplus_match = re.search(r"(\d+)\s*بر\s*مازاد|مازاد\s*بر\s*(\d+)", tail)
        if bound_match:
            hi, lo = _num(bound_match.group(1)), _num(bound_match.group(2))
            tiers.append({"from_kwh": lo, "to_kwh": hi, "rate_rial_per_kwh": _num(rate),
                          "quantity_kwh": _num(qty), "amount_rial": _num(amount)})
        elif surplus_match:
            lo = _num(surplus_match.group(1) or surplus_match.group(2))
            tiers.append({"from_kwh": lo, "to_kwh": None, "rate_rial_per_kwh": _num(rate),
                          "quantity_kwh": _num(qty), "amount_rial": _num(amount)})
    result["tariff_tiers_30d"] = tiers

    m = re.search(r"([\d,]+)\s+([\d,]+)\s+میزان\s*مصرف\s*در\s*اوج\s*بار", text)
    if m:
        result["peak_load_surcharge"] = {
            "quantity_kwh": _num(m.group(1)), "rate_rial_per_kwh": _num(m.group(2)),
            "amount_rial": _num(_search(r"([\d,]+)\s+اضافه\s*اوج\s*بار", text)),
        }
    m = re.search(r"([\d,]+)\s+(-?[\d,]+)\s+میزان\s*مصرف\s*درکم\s*باری", text)
    if m:
        result["off_peak_discount"] = {
            "quantity_kwh": _num(m.group(1)), "rate_rial_per_kwh": _num(m.group(2)),
            "amount_rial": _num(_search(r"(-?[\d,]+)\s+تخفیف\s*کم\s*باری", text)),
        }

    # اعتبارسنجی متقابل: مبلغ مصرف پایه ۳۰ روزه‌نرمال‌شده که سپس بر مبنای
    # تعداد روز واقعی دوره، نسبت‌گیری می‌شود — فرمول واقعی محاسبه انرژی در ایران
    m = re.search(r"\(\s*(\d+)\s*÷\s*30\s*\)\s*×\s*(\d+)", text)
    if m:
        result["energy_charge_30d_normalized_rial"] = _num(m.group(1))
        result["proration_days"] = _num(m.group(2))

    charge_fields = {
        "energy_charge_rial": r"مبلغ\s*مصرف\s+([\d,]+)(?!ريال)",
        "seasonal_peak_charge_rial": r"پیک\s*فصلی\s+([\d,]+)",
        "subscription_fee_rial": r"آبونمان\s+([\d,]+)",
        "electricity_duty_rial": r"عوارض\s*برق\s+([\d,]+)",
        "insurance_fee_rial": r"مبلغ\s*بیمه\s+([\d,]+)",
        "power_plant_fuel_charge_rial": r"سوخت\s*نیروگاهی\s+([\d,]+)",
        "subtotal_before_vat_rial": r"مبلغ\s*صورتحساب\s*دوره\s+([\d,]+)",
        "vat_rial": r"مالیات\s*بر\s*ارزش\s*افزوده\s+([\d,]+)",
        "previous_balance_rial": r"بدهکاری\s*[/\\]\s*بستانکاری\s+([\d,]+)",
        "rounding_adjustment_rial": r"کسر\s*هزار\s*ريال\s+(-?[\d,]+)",
    }
    for key, pattern in charge_fields.items():
        result[key] = _num(_search(pattern, text))

    result["amount_payable_rial"] = _num(_search(r"([\d,]+)\s+مبلغ\s*قابل\s*پرداخت", text))

    result["tariff_category_detected"] = (
        "general_tou_block" if not re.search(r"دیماند|ضریب\s*قدرت", text) else "industrial_demand_pf"
    )
    return result


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/قبض_برق.pdf"
    print(json.dumps(parse_bill(path), ensure_ascii=False, indent=2))
