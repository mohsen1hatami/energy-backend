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

import pandas as pd
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


@app.post("/api/site/build")
async def api_site_build(
    pq_file: UploadFile = File(...),
    bill_file: Optional[UploadFile] = File(None),
):
    """می‌سازد یک شیء «سایت» کاملاً واقعی، دقیقاً با همان ساختاری که پنل‌های
    داشبورد اصلی (دیوار دیماند، روند مصرف، کیفیت توان، هزینه ماهانه، هشدارها،
    شبیه‌ساز اگر/آنگاه) انتظار دارند — تا بشود جایگزین دادهٔ نمایشی کرد.

    محدودیت‌های صادقانه (چون داده واقعی محدودیت‌های واقعی دارد):
      - «مورد انتظار» (baseline) یک میانگین آماری ساده است (میانگین همان ساعت
        از شبانه‌روز / میانگین متحرک روزانه)، نه یک مدل یادگیری‌ماشین آموزش‌دیده
        — چون دمای هوا در خروجی کنتور نیست و حجم داده برای آموزش مدل واقعی کم است.
      - نوع تعرفه از روی ستون «تعرفه» در خود فایل کیفیت توان تشخیص داده می‌شود
        (صنعتی/تجاری -> مدل دیماند+ضریب‌قدرت ؛ در غیر این صورت -> مدل پلکانی).
      - اگر قبض واقعی همین مشترک را ندهید، نرخ‌های دیماند/کارمزد به‌صورت
        برآوردی (placeholder) هستند — این در پاسخ مشخص می‌شود.
    """
    if not pq_file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "فایل کیفیت توان باید CSV یا XLSX باشد")

    pq_suffix = ".xlsx" if pq_file.filename.lower().endswith(".xlsx") else (
        ".xls" if pq_file.filename.lower().endswith(".xls") else ".csv"
    )
    pq_path = _save_upload(pq_file, pq_suffix)
    bill_path = None
    if bill_file is not None:
        if not bill_file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, "فایل قبض باید PDF باشد")
        bill_path = _save_upload(bill_file, ".pdf")

    try:
        df = import_pq_csv(pq_path)
        if len(df) == 0:
            raise HTTPException(422, "فایل کیفیت توان هیچ ردیفی ندارد")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        if len(df) < 2:
            raise HTTPException(422, "داده کافی برای تحلیل نیست")

        diffs = df["timestamp"].diff().dropna().dt.total_seconds() / 3600.0
        interval_hours = float(diffs.median()) if len(diffs) else 1.0
        df["date"] = df["timestamp"].dt.date
        df["hour"] = df["timestamp"].dt.hour
        df["kwh"] = df["active_power_kw"] * interval_hours

        # --- تشخیص نوع تعرفه از روی خود داده کیفیت توان ---
        raw_tariff_codes = " ".join(df["tariff_code"].dropna().astype(str).unique()) if "tariff_code" in df else ""
        is_industrial = any(k in raw_tariff_codes for k in ["صنعت", "تجار"])

        bill = None
        if bill_path:
            try:
                bill = parse_bill(bill_path)
            except Exception:
                bill = None

        # --- سری روزانه با پایه آماری ساده (میانگین متحرک ۷ روزه) ---
        daily = df.groupby("date").agg(
            actual_kwh=("kwh", "sum"), avg_pf=("power_factor", "mean"),
            avg_thd=("thd_percent", "mean"), peak_kw=("active_power_kw", "max"),
        ).reset_index()
        daily["expected_kwh"] = daily["actual_kwh"].rolling(7, min_periods=1).mean().shift(1)
        daily["expected_kwh"] = daily["expected_kwh"].fillna(daily["actual_kwh"].mean())
        daily_series = [
            {
                "timestamp": str(r.date), "actual_kwh": round(r.actual_kwh, 2),
                "expected_kwh": round(r.expected_kwh, 2),
                "avg_pf": round(r.avg_pf, 3) if pd.notna(r.avg_pf) else None,
                "avg_thd": round(r.avg_thd, 2) if pd.notna(r.avg_thd) else None,
                "peak_kw": round(r.peak_kw, 2),
            }
            for r in daily.itertuples()
        ]

        # --- پروفایل ساعتی نمونه: میانگین هر ساعت از شبانه‌روز در کل بازه ---
        hourly = df.groupby("hour").agg(
            active_power_kw=("active_power_kw", "mean"), power_factor=("power_factor", "mean"),
            thd_percent=("thd_percent", "mean"),
        ).reset_index()
        hourly_avg_map = dict(zip(hourly["hour"], hourly["active_power_kw"]))
        hourly_profile_sample = [
            {
                "hour": int(r.hour), "active_power_kw": round(r.active_power_kw, 3),
                "expected_power_kw": round(hourly_avg_map.get(r.hour, r.active_power_kw), 3),
                "power_factor": round(r.power_factor, 3) if pd.notna(r.power_factor) else None,
                "thd_percent": round(r.thd_percent, 2) if pd.notna(r.thd_percent) else None,
            }
            for r in hourly.itertuples()
        ]

        # --- هشدارهای قطعی (rule-based)، مرتب بر اساس شدت ---
        events = []
        for r in df.itertuples():
            reasons, severity = [], 0
            if pd.notna(r.power_factor) and r.power_factor < 0.85:
                reasons.append(f"افت ضریب قدرت به {round(r.power_factor,2)}")
                severity = max(severity, 3 if r.power_factor < 0.7 else 2)
            if pd.notna(getattr(r, "voltage_imbalance_percent", None)) and r.voltage_imbalance_percent > 3:
                reasons.append(f"عدم تعادل فاز {round(r.voltage_imbalance_percent,1)}٪")
                severity = max(severity, 2)
            if pd.notna(r.thd_percent) and r.thd_percent > 8:
                reasons.append(f"THD بالا ({round(r.thd_percent,1)}٪)")
                severity = max(severity, 2)
            if pd.notna(r.contract_demand_kw) and r.active_power_kw > r.contract_demand_kw:
                reasons.append("عبور از دیماند قراردادی")
                severity = 3
            if reasons:
                events.append({
                    "timestamp": str(r.timestamp), "reasons": reasons, "severity": severity,
                    "power_factor": round(r.power_factor, 3) if pd.notna(r.power_factor) else None,
                    "thd_percent": round(r.thd_percent, 2) if pd.notna(r.thd_percent) else None,
                    "active_power_kw": round(r.active_power_kw, 2),
                })
        anomaly_count = len(events)
        events.sort(key=lambda e: e["severity"], reverse=True)
        anomalies = events[:12]

        total_kwh = float(df["kwh"].sum())
        peak_demand_kw = float(df["registered_demand_kw"].max()) if df["registered_demand_kw"].notna().any() else float(df["active_power_kw"].max())
        contract_demand_kw = float(df["contract_demand_kw"].dropna().iloc[0]) if df["contract_demand_kw"].notna().any() else None
        avg_pf_all = float(df["power_factor"].mean()) if df["power_factor"].notna().any() else None
        avg_thd_all = float(df["thd_percent"].mean()) if df["thd_percent"].notna().any() else None
        days_covered = int(df["date"].nunique())

        mape = float((abs(daily["actual_kwh"] - daily["expected_kwh"]) / daily["actual_kwh"].replace(0, pd.NA)).mean() * 100) if len(daily) else None
        mae = float(abs(daily["actual_kwh"] - daily["expected_kwh"]).mean()) if len(daily) else None

        # --- هزینه: انتخاب مدل بر مبنای نوع تعرفه واقعی ---
        cost_notes = []
        monthly_bills, what_if, total_bill_rial = [], {}, None
        if is_industrial:
            from tariff_engine import TariffConfig, monthly_bill as industrial_monthly_bill
            cfg_i = TariffConfig()
            cost_notes.append(
                "این مشترک صنعتی/تجاری تشخیص داده شد. نرخ کارمزد دیماند در این نسخه برآوردی است — "
                "برای دقت واقعی، قبض برق همین مشترک (با ردیف دیماند و جریمه ضریب قدرت) را هم آپلود کنید."
            )
            hourly_df = df.set_index("timestamp")[["active_power_kw", "power_factor"]].resample("1h").mean().dropna().reset_index()
            hourly_df["hour"] = hourly_df["timestamp"].dt.hour
            mb = industrial_monthly_bill(hourly_df, cfg_i)
            monthly_bills = [
                {
                    "year_month": r.year_month, "energy_cost_rial": round(r.energy_cost_rial),
                    "demand_charge_rial": round(r.demand_charge_rial), "pf_penalty_rial": round(r.pf_penalty_rial),
                    "total_bill_rial": round(r.total_bill_rial),
                }
                for r in mb.itertuples()
            ]
            total_bill_rial = sum(m["total_bill_rial"] for m in monthly_bills) or None
            base_annual = (total_bill_rial or 0) / max(days_covered, 1) * 365
            what_if = {
                "pf_correction": {"annualized_savings_rial": round(base_annual * 0.06), "savings_percent": 6.0},
                "peak_shift": {"annualized_savings_rial": round(base_annual * 0.04), "savings_percent": 4.0},
                "combined": {"annualized_savings_rial": round(base_annual * 0.09), "savings_percent": 9.0},
                "baseline_annualized_bill_rial": round(base_annual),
            }
            cost_notes.append("اعداد شبیه‌ساز اگر/آنگاه برای این مشترک صنعتی هنوز به‌صورت درصد تقریبی است، نه محاسبه دقیق پلکانی.")
        else:
            from tariff_engine import calibrate_general_tariff_from_bill, calculate_general_tou_bill
            cfg_g = calibrate_general_tariff_from_bill(bill) if (bill and bill.get("tariff_tiers_30d")) else calibrate_general_tariff_from_bill({})
            if not (bill and bill.get("tariff_tiers_30d")):
                cost_notes.append("قبض واقعی این مشترک داده نشد؛ از نرخ‌های پیش‌فرض پلکانی استفاده شد.")
            peak_kwh_total = float(df.loc[df["hour"].between(19, 22), "kwh"].sum())
            offpeak_kwh_total = float(df.loc[(df["hour"] >= 23) | (df["hour"] < 7), "kwh"].sum())
            result = calculate_general_tou_bill(total_kwh, peak_kwh_total, offpeak_kwh_total, days_covered, cfg_g)
            total_bill_rial = result["amount_payable_rial"]
            annualized = total_bill_rial / max(days_covered, 1) * 365
            what_if = {
                "pf_correction": {"annualized_savings_rial": 0, "savings_percent": 0.0},
                "peak_shift": {"annualized_savings_rial": round(annualized * 0.05), "savings_percent": 5.0},
                "combined": {"annualized_savings_rial": round(annualized * 0.05), "savings_percent": 5.0},
                "baseline_annualized_bill_rial": round(annualized),
            }
            cost_notes.append("تفکیک ساعتی اوج‌بار/کم‌باری برای این تخمین از یک زمان‌بندی معمول (اوج ۱۹-۲۳، کم‌باری ۲۳-۷) استفاده کرده، نه زمان‌بندی رسمی دقیق منطقه شما.")

        subscriber_name = (bill or {}).get("subscriber_name")
        if not subscriber_name and "first_name" in df.columns:
            pass

        site = {
            "site_id": "real_site",
            "name": subscriber_name or "مشترک واقعی شما",
            "profile": "industrial" if is_industrial else "general",
            "kpis": {
                "total_kwh": round(total_kwh, 1), "total_bill_rial": total_bill_rial,
                "peak_demand_kw": round(peak_demand_kw, 2), "contract_demand_kw": contract_demand_kw,
                "avg_power_factor": round(avg_pf_all, 3) if avg_pf_all is not None else None,
                "avg_thd_percent": round(avg_thd_all, 2) if avg_thd_all is not None else None,
                "anomaly_count": anomaly_count,
                "model_mape_percent": round(mape, 2) if mape is not None else None,
                "model_mae_kw": round(mae, 2) if mae is not None else None,
                "days_covered": days_covered,
            },
            "daily_series": daily_series,
            "hourly_profile_sample": hourly_profile_sample,
            "anomalies": anomalies,
            "monthly_bills": monthly_bills,
            "what_if": what_if,
            "notes": cost_notes,
        }
        return site
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"خطا در ساخت داشبورد واقعی: {e}")
    finally:
        os.remove(pq_path)
        if bill_path:
            os.remove(bill_path)


@app.post("/api/pq/analyze")
async def api_pq_analyze(file: UploadFile = File(...)):
    """بر خلاف /api/pq/preview (که فقط ساختار ستون‌ها را نشان می‌دهد)، این
    اندپوینت آمار واقعی، یک سری زمانی برای نمودار، و پرچم‌های ناهنجاری
    قطعی (rule-based) را برمی‌گرداند — بدون نیاز به آموزش مدل یادگیری ماشین،
    چون برای یک فایل تکی معمولاً داده کافی برای آموزش معنادار مدل نیست.
    """
    if not file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "فقط فایل CSV یا XLSX پذیرفته می‌شود")
    suffix = ".xlsx" if file.filename.lower().endswith(".xlsx") else (
        ".xls" if file.filename.lower().endswith(".xls") else ".csv"
    )
    path = _save_upload(file, suffix)
    try:
        df = import_pq_csv(path)
        if len(df) == 0:
            raise HTTPException(422, "فایل هیچ ردیف داده‌ای ندارد — فقط هدر دارد")
        if df["timestamp"].isna().all():
            raise HTTPException(422, "ستون تاریخ/زمان خوانده نشد؛ ساختار فایل را بررسی کنید")

        df = df.sort_values("timestamp")
        step = max(1, len(df) // 500)
        sampled = df.iloc[::step].copy()
        sampled["timestamp"] = sampled["timestamp"].astype(str)
        series = sampled[[
            "timestamp", "active_power_kw", "power_factor", "thd_percent", "voltage_imbalance_percent"
        ]].fillna(0).to_dict(orient="records")

        summary = {
            "row_count": int(len(df)),
            "start": str(df["timestamp"].min()),
            "end": str(df["timestamp"].max()),
            "avg_power_factor": round(float(df["power_factor"].mean()), 3) if df["power_factor"].notna().any() else None,
            "min_power_factor": round(float(df["power_factor"].min()), 3) if df["power_factor"].notna().any() else None,
            "avg_thd_percent": round(float(df["thd_percent"].mean()), 2) if df["thd_percent"].notna().any() else None,
            "max_thd_percent": round(float(df["thd_percent"].max()), 2) if df["thd_percent"].notna().any() else None,
            "avg_voltage_imbalance_percent": round(float(df["voltage_imbalance_percent"].mean()), 2) if df["voltage_imbalance_percent"].notna().any() else None,
            "peak_active_power_kw": round(float(df["active_power_kw"].max()), 1) if df["active_power_kw"].notna().any() else None,
            "contract_demand_kw": float(df["contract_demand_kw"].dropna().iloc[0]) if df["contract_demand_kw"].notna().any() else None,
        }

        flags = []
        if summary["min_power_factor"] is not None and summary["min_power_factor"] < 0.85:
            n = int((df["power_factor"] < 0.85).sum())
            flags.append({"type": "افت ضریب قدرت", "count": n, "detail": f"{n} بازه با PF زیر ۰٫۸۵ (کمینه {summary['min_power_factor']})"})
        if summary["max_thd_percent"] is not None and summary["max_thd_percent"] > 8:
            n = int((df["thd_percent"] > 8).sum())
            flags.append({"type": "اعوجاج هارمونیکی بالا", "count": n, "detail": f"{n} بازه با THD بالای ۸٪ (بیشینه {summary['max_thd_percent']}٪)"})
        if summary["avg_voltage_imbalance_percent"] is not None and df["voltage_imbalance_percent"].max() > 3:
            n = int((df["voltage_imbalance_percent"] > 3).sum())
            flags.append({"type": "عدم تعادل فاز", "count": n, "detail": f"{n} بازه با عدم تعادل ولتاژ بالای ۳٪"})
        if summary["contract_demand_kw"] and summary["peak_active_power_kw"] and summary["peak_active_power_kw"] > summary["contract_demand_kw"]:
            flags.append({"type": "عبور از دیماند قراردادی", "count": None, "detail": f"اوج مصرف {summary['peak_active_power_kw']} kW بیشتر از قدرت قراردادی {summary['contract_demand_kw']} kW"})

        return {"summary": summary, "series": series, "flags": flags}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"خطا در تحلیل فایل کیفیت توان: {e}")
    finally:
        os.remove(path)
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


def _rule_based_anomalies(df: pd.DataFrame, site_id: str, limit: int = 12) -> list:
    """پرچم‌گذاری قطعی (rule-based) روی هر ردیف — همان آستانه‌هایی که در
    /api/pq/analyze استفاده شد، اینجا در قالب فهرست «رویداد» (نه فقط شمارش)
    برمی‌گردد تا در پنل هشدارهای داشبورد قابل‌نمایش باشد."""
    events = []
    for _, row in df.iterrows():
        reasons = []
        if pd.notna(row.get("power_factor")) and row["power_factor"] < 0.85:
            reasons.append("افت ضریب قدرت")
        if pd.notna(row.get("thd_percent")) and row["thd_percent"] > 8:
            reasons.append("اعوجاج هارمونیکی بالا")
        if pd.notna(row.get("voltage_imbalance_percent")) and row["voltage_imbalance_percent"] > 3:
            reasons.append("عدم تعادل فاز")
        if (
            pd.notna(row.get("contract_demand_kw")) and pd.notna(row.get("active_power_kw"))
            and row["active_power_kw"] > row["contract_demand_kw"]
        ):
            reasons.append("عبور از دیماند قراردادی")
        if reasons:
            severity = min(3, len(reasons) + (1 if "عبور از دیماند قراردادی" in reasons else 0))
            events.append({
                "timestamp": str(row["timestamp"]),
                "site_id": site_id,
                "reasons": reasons,
                "severity": severity,
                "power_factor": None if pd.isna(row.get("power_factor")) else round(float(row["power_factor"]), 3),
                "thd_percent": None if pd.isna(row.get("thd_percent")) else round(float(row["thd_percent"]), 2),
                "active_power_kw": None if pd.isna(row.get("active_power_kw")) else round(float(row["active_power_kw"]), 2),
            })
    events.sort(key=lambda e: e["severity"], reverse=True)
    return events[:limit], len(events)


@app.post("/api/site/build")
async def api_site_build(
    pq_file: UploadFile = File(...),
    bill_file: Optional[UploadFile] = File(None),
    site_name: Optional[str] = Form(None),
):
    """ساخت یک «سایت» کامل و واقعی (همان شکل دادهٔ داشبورد) مستقیماً از
    فایل کیفیت توان واقعی (اجباری) + قبض واقعی (اختیاری، برای هزینه/تعرفه).
    خروجی این اندپوینت مستقیماً جایگزین سایت‌های نمونه در داشبورد می‌شود.
    """
    if not pq_file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "فایل کیفیت توان باید CSV یا Excel باشد")
    pq_suffix = ".xlsx" if pq_file.filename.lower().endswith(".xlsx") else (
        ".xls" if pq_file.filename.lower().endswith(".xls") else ".csv"
    )
    pq_path = _save_upload(pq_file, pq_suffix)
    bill_path = None
    if bill_file is not None:
        if not bill_file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, "فایل قبض باید PDF باشد")
        bill_path = _save_upload(bill_file, ".pdf")

    try:
        df = import_pq_csv(pq_path)
        if len(df) == 0:
            raise HTTPException(422, "فایل کیفیت توان هیچ ردیف داده‌ای ندارد")
        if df["timestamp"].isna().all():
            raise HTTPException(422, "ستون تاریخ/زمان فایل کیفیت توان خوانده نشد")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp")

        # تشخیص خودکار فاصله بین قرائت‌ها (برای تبدیل توان به انرژی kWh)
        diffs = df["timestamp"].diff().dropna().dt.total_seconds() / 3600.0
        interval_h = float(diffs.median()) if len(diffs) else 0.25

        df["date"] = df["timestamp"].dt.date
        daily = df.groupby("date").agg(
            actual_kwh=("active_power_kw", lambda s: round(float((s * interval_h).sum()), 2)),
            avg_pf=("power_factor", "mean"),
            avg_thd=("thd_percent", "mean"),
            peak_kw=("active_power_kw", "max"),
        ).reset_index()
        daily_series = [
            {
                "timestamp": str(r["date"]),
                "actual_kwh": r["actual_kwh"],
                "expected_kwh": None,
                "avg_pf": None if pd.isna(r["avg_pf"]) else round(float(r["avg_pf"]), 3),
                "avg_thd": None if pd.isna(r["avg_thd"]) else round(float(r["avg_thd"]), 2),
                "peak_kw": round(float(r["peak_kw"]), 2),
            }
            for _, r in daily.iterrows()
        ]

        busiest_date = daily.sort_values("peak_kw", ascending=False).iloc[0]["date"]
        day_df = df[df["date"] == busiest_date]
        hourly = day_df.groupby(day_df["timestamp"].dt.hour).agg(
            active_power_kw=("active_power_kw", "mean"),
            power_factor=("power_factor", "mean"),
            thd_percent=("thd_percent", "mean"),
        )
        hourly_profile_sample = [
            {
                "hour": h,
                "active_power_kw": round(float(hourly.loc[h, "active_power_kw"]), 2) if h in hourly.index else 0,
                "expected_power_kw": None,
                "power_factor": (
                    round(float(hourly.loc[h, "power_factor"]), 3)
                    if h in hourly.index and pd.notna(hourly.loc[h, "power_factor"]) else None
                ),
                "thd_percent": (
                    round(float(hourly.loc[h, "thd_percent"]), 2)
                    if h in hourly.index and pd.notna(hourly.loc[h, "thd_percent"]) else None
                ),
            }
            for h in range(24)
        ]

        anomalies, anomaly_count = _rule_based_anomalies(df, "real_1")
        contract_demand = (
            float(df["contract_demand_kw"].dropna().iloc[0]) if df["contract_demand_kw"].notna().any() else None
        )

        kpis = {
            "total_kwh": round(float(daily["actual_kwh"].sum()), 1),
            "peak_demand_kw": round(float(df["active_power_kw"].max()), 2),
            "contract_demand_kw": contract_demand,
            "avg_power_factor": round(float(df["power_factor"].mean()), 3) if df["power_factor"].notna().any() else None,
            "avg_thd_percent": round(float(df["thd_percent"].mean()), 2) if df["thd_percent"].notna().any() else None,
            "anomaly_count": anomaly_count,
            "model_mape_percent": None,
            "days_covered": int(len(daily)),
        }

        site = {
            "site_id": "real_1",
            "name": site_name or "مشترک آپلودشده",
            "profile": "real",
            "kpis": kpis,
            "daily_series": daily_series,
            "hourly_profile_sample": hourly_profile_sample,
            "anomalies": anomalies,
            "monthly_bills": [],
            "what_if": None,
        }

        if bill_path:
            try:
                bill = parse_bill(bill_path)
                cfg = calibrate_general_tariff_from_bill(bill)
                cons = bill.get("consumption_by_band_kwh") or {}
                site["name"] = bill.get("subscriber_name") or site["name"]
                site["monthly_bills"] = [{
                    "year_month": (bill.get("issue_date_jalali") or "")[:7],
                    "energy_cost_rial": bill.get("energy_charge_rial"),
                    "demand_charge_rial": 0,
                    "pf_penalty_rial": 0,
                    "total_bill_rial": bill.get("amount_payable_rial"),
                }]
                kpis["total_bill_rial"] = bill.get("amount_payable_rial")

                if cons and bill.get("total_consumption_kwh") and bill.get("days_covered"):
                    baseline = calculate_general_tou_bill(
                        bill["total_consumption_kwh"], cons.get("peak_load", 0), cons.get("low_load", 0),
                        bill["days_covered"], cfg,
                    )
                    peak = cons.get("peak_load", 0)
                    scenarios = {}
                    for key, (rm, sh) in {
                        "pf_correction": (0.0, 0.0),
                        "peak_shift": (0.0, 60.0),
                        "combined": (10.0, 60.0),
                    }.items():
                        removed = peak * rm / 100.0
                        shifted = (peak - removed) * sh / 100.0
                        sc = calculate_general_tou_bill(
                            bill["total_consumption_kwh"] - removed, peak - removed - shifted,
                            cons.get("low_load", 0) + shifted, bill["days_covered"], cfg,
                        )
                        savings = baseline["amount_payable_rial"] - sc["amount_payable_rial"]
                        scenarios[key] = {
                            "annualized_savings_rial": round(savings * 365 / bill["days_covered"]),
                            "savings_percent": round(savings / baseline["amount_payable_rial"] * 100, 2),
                        }
                    scenarios["baseline_annualized_bill_rial"] = round(
                        baseline["amount_payable_rial"] * 365 / bill["days_covered"]
                    )
                    site["what_if"] = scenarios
            except Exception:
                pass  # اگر قبض قابل‌پارس نبود، بخش کیفیت توان همچنان برگردانده می‌شود
            finally:
                os.remove(bill_path)

        return site
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"خطا در ساخت داشبورد از داده واقعی: {e}")
    finally:
        os.remove(pq_path)
