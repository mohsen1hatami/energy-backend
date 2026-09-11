# -*- coding: utf-8 -*-
"""
API سرویس آپلود و تحلیل قبض/کیفیت توان — برای دیپلوی روی Render.

اندپوینت‌ها:
  GET  /health                بررسی سلامت سرویس
  POST /api/bill/parse        آپلود PDF قبض -> خروجی ساختاریافته parse_bill
  POST /api/tariff/estimate   آپلود PDF قبض + پارامتر سناریو -> مقایسه هزینه پایه/سناریو
  POST /api/pq/preview        آپلود CSV کیفیت توان -> پیش‌نمایش نرمال‌شده + تشخیص ستون‌ها

⚠️ این نسخه اولیه (MVP) عمداً ساده نگه داشته شده: محدودیت حجم فایل و پاکسازی
فایل موقت دارد، اما احراز هویت/نرخ‌محدودسازی ندارد. قبل از استفاده عمومی
گسترده، این موارد را اضافه کنید.
"""
import os
import shutil
import tempfile
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from bill_parser import parse_bill
from pq_import import import_pq_csv
from tariff_engine import calibrate_general_tariff_from_bill, calculate_general_tou_bill

MAX_UPLOAD_MB = 10

app = FastAPI(title="Energy Platform API", version="0.1.0")

allowed_origins = os.environ.get("ALLOWED_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins.split(",")] if allowed_origins != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _save_upload(file: UploadFile, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    with os.fdopen(fd, "wb") as out:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                out.close()
                os.remove(path)
                raise HTTPException(413, f"حجم فایل نباید بیشتر از {MAX_UPLOAD_MB} مگابایت باشد")
            out.write(chunk)
    return path


@app.get("/")
def root():
    return {"service": "Energy Platform API", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/bill/parse")
async def api_parse_bill(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "فقط فایل PDF پذیرفته می‌شود")
    path = _save_upload(file, ".pdf")
    try:
        return parse_bill(path)
    except Exception as e:
        raise HTTPException(422, f"خطا در تحلیل قبض: {e}")
    finally:
        os.remove(path)


@app.post("/api/tariff/estimate")
async def api_tariff_estimate(
    file: UploadFile = File(...),
    peak_reduction_percent: float = Form(0.0),
    shift_to_offpeak_percent: float = Form(0.0),
):
    """آپلود قبض + دو پارامتر سناریو اختیاری:
    - peak_reduction_percent: چند درصد از مصرف اوج‌بار کلاً حذف می‌شود (مثلاً با خاموش کردن تجهیز غیرضروری)
    - shift_to_offpeak_percent: چند درصد از مصرف اوج‌بار به کم‌باری منتقل می‌شود (مثلاً با زمان‌بندی مجدد بار)
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "فقط فایل PDF پذیرفته می‌شود")
    path = _save_upload(file, ".pdf")
    try:
        bill = parse_bill(path)
        cfg = calibrate_general_tariff_from_bill(bill)
        cons = bill.get("consumption_by_band_kwh") or {}
        if not cons or bill.get("total_consumption_kwh") is None or bill.get("days_covered") is None:
            raise HTTPException(422, "اطلاعات مصرف از این قبض به‌طور کامل استخراج نشد")

        baseline = calculate_general_tou_bill(
            bill["total_consumption_kwh"], cons.get("peak_load", 0), cons.get("low_load", 0),
            bill["days_covered"], cfg,
        )

        peak = cons.get("peak_load", 0)
        removed = peak * peak_reduction_percent / 100.0
        shifted = (peak - removed) * shift_to_offpeak_percent / 100.0
        new_peak = peak - removed - shifted
        new_offpeak = cons.get("low_load", 0) + shifted
        new_total = bill["total_consumption_kwh"] - removed

        scenario = calculate_general_tou_bill(new_total, new_peak, new_offpeak, bill["days_covered"], cfg)

        return {
            "subscriber_name": bill.get("subscriber_name"),
            "tariff_title": bill.get("tariff_title"),
            "tariff_category_detected": bill.get("tariff_category_detected"),
            "baseline": baseline,
            "scenario": scenario,
            "estimated_savings_rial": baseline["amount_payable_rial"] - scenario["amount_payable_rial"],
            "estimated_savings_percent": round(
                (baseline["amount_payable_rial"] - scenario["amount_payable_rial"])
                / baseline["amount_payable_rial"] * 100, 2,
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"خطا در محاسبه: {e}")
    finally:
        os.remove(path)


@app.post("/api/pq/preview")
async def api_pq_preview(file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "فقط فایل CSV یا XLSX پذیرفته می‌شود")
    if file.filename.lower().endswith(".xlsx"):
        suffix = ".xlsx"
    elif file.filename.lower().endswith(".xls"):
        suffix = ".xls"
    else:
        suffix = ".csv"
    path = _save_upload(file, suffix)
    try:
        df = import_pq_csv(path)
        return {
            "row_count": len(df),
            "columns": list(df.columns),
            "sample": df.head(5).fillna("").astype(str).to_dict(orient="records"),
            "note": (
                "فایل ورودی هیچ ردیف داده‌ای نداشت — فقط ساختار ستون‌ها تأیید شد."
                if len(df) == 0 else None
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"خطا در خواندن فایل کیفیت توان: {e}")
    finally:
        os.remove(path)


@app.post("/api/bills/batch")
async def api_bills_batch(files: list[UploadFile] = File(...)):
    """آپلود هم‌زمان چند قبض PDF (مثلاً ۱۲ ماه گذشته) -> فهرست دوره‌ها،
    مرتب‌شده بر اساس تاریخ، برای رسم روند مصرف/هزینه در طول زمان.
    این راه، بدون نیاز به داده لحظه‌ای کنتور هوشمند، یک تاریخچه واقعی از
    روی خودِ قبض‌ها می‌سازد.
    """
    parsed, errors = [], []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            errors.append({"filename": f.filename, "error": "فقط فایل PDF پذیرفته می‌شود"})
            continue
        path = _save_upload(f, ".pdf")
        try:
            bill = parse_bill(path)
            bill["_source_filename"] = f.filename
            parsed.append(bill)
        except Exception as e:
            errors.append({"filename": f.filename, "error": str(e)})
        finally:
            os.remove(path)

    def sort_key(b):
        d = b.get("issue_date_jalali") or b.get("meter_readings", {}).get("current", {}).get("date_jalali") or ""
        return d

    parsed.sort(key=sort_key)
    total_kwh = sum(b.get("total_consumption_kwh") or 0 for b in parsed)
    total_payable = sum(b.get("amount_payable_rial") or 0 for b in parsed)

    return {
        "periods_count": len(parsed),
        "bills": parsed,
        "errors": errors,
        "summary": {
            "total_consumption_kwh": total_kwh,
            "total_payable_rial": total_payable,
            "avg_monthly_payable_rial": round(total_payable / len(parsed)) if parsed else None,
        },
    }
