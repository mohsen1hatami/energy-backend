# -*- coding: utf-8 -*-
"""
build_dashboard_data.py
-------------------------
اجرای کل خط لوله (pipeline) برای هر دو سایت نمونه و تولید یک فایل JSON فشرده
که مستقیماً توسط frontend/dashboard.html مصرف می‌شود.

اجرا:
    cd backend && python3 build_dashboard_data.py
"""

import json
import numpy as np
import pandas as pd

from data_simulator import generate_site_data
from tariff_engine import TariffConfig, apply_tariff, monthly_bill
from ml_models import train_baseline_forecast_model, add_expected_load, detect_anomalies
from what_if_engine import run_what_if_scenarios


SITE_DEFS = [
    {"site_id": "site_A", "profile": "industrial", "name": "کارخانه نساجی الف",
     "contract_demand_kw": 550},
    {"site_id": "site_B", "profile": "commercial", "name": "مجتمع اداری-تجاری ب",
     "contract_demand_kw": 220},
]


def build_site_payload(site_def: dict, cfg: TariffConfig) -> dict:
    df = generate_site_data(
        site_id=site_def["site_id"],
        profile=site_def["profile"],
        contract_demand_kw=site_def["contract_demand_kw"],
    )

    # --- مدل یادگیری ماشین خط مبنا (Baseline / IPMVP Option-C style)
    model, model_metrics = train_baseline_forecast_model(df)
    df = add_expected_load(df, model)

    # --- تعرفه و صورتحساب
    df_tariff = apply_tariff(df, cfg)
    bills = monthly_bill(df, cfg)

    # --- ناهنجاری‌ها
    anomalies_df = detect_anomalies(df)
    top_anomalies = anomalies_df.head(12) if len(anomalies_df) else anomalies_df

    # --- سناریوهای اگر...آنگاه
    what_if = run_what_if_scenarios(df, cfg)

    # --- تجمیع روزانه برای نمودار روند
    daily = df.groupby(df["timestamp"].dt.date).agg(
        actual_kwh=("active_power_kw", "sum"),
        expected_kwh=("expected_power_kw", "sum"),
        avg_pf=("power_factor", "mean"),
        avg_thd=("thd_percent", "mean"),
        peak_kw=("active_power_kw", "max"),
    ).reset_index()
    daily["timestamp"] = daily["timestamp"].astype(str)

    # --- یک روز کاری نماینده برای نمودار "دیوار دیماند" ساعتی
    sample_day = df[df["timestamp"].dt.date == df["timestamp"].dt.date.iloc[24 * 20]]
    hourly_profile = sample_day[[
        "hour", "active_power_kw", "expected_power_kw", "power_factor", "thd_percent"
    ]].round(2).to_dict(orient="records")

    kpis = {
        "total_kwh": round(float(df["active_power_kw"].sum()), 0),
        "total_bill_rial": round(float(bills["total_bill_rial"].sum()), 0),
        "peak_demand_kw": round(float(df["active_power_kw"].max()), 1),
        "contract_demand_kw": site_def["contract_demand_kw"],
        "avg_power_factor": round(float(df["power_factor"].mean()), 3),
        "avg_thd_percent": round(float(df["thd_percent"].mean()), 2),
        "anomaly_count": int(len(anomalies_df)),
        "model_mape_percent": model_metrics["mape_percent"],
        "model_mae_kw": model_metrics["mae_kw"],
        "days_covered": int(df["timestamp"].dt.date.nunique()),
    }

    return {
        "site_id": site_def["site_id"],
        "name": site_def["name"],
        "profile": site_def["profile"],
        "kpis": kpis,
        "daily_series": daily.round(2).to_dict(orient="records"),
        "hourly_profile_sample": hourly_profile,
        "anomalies": top_anomalies.to_dict(orient="records") if len(top_anomalies) else [],
        "monthly_bills": bills.round(0).to_dict(orient="records"),
        "what_if": what_if,
    }


def main():
    cfg = TariffConfig()
    payload = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "tariff_assumptions": {
            "base_rate_rial_per_kwh": cfg.base_rate_rial_per_kwh,
            "off_peak_multiplier": cfg.off_peak_multiplier,
            "mid_peak_multiplier": cfg.mid_peak_multiplier,
            "peak_multiplier": cfg.peak_multiplier,
            "demand_charge_rial_per_kw": cfg.demand_charge_rial_per_kw,
            "pf_target": cfg.pf_target,
            "note": "نرخ انرژی بر اساس آخرین نرخ‌نامه ۱۴۰۵ مشترکان تولید (صنعت و معدن) — سایر پارامترها فرضی/قابل‌تنظیم‌اند و باید از قبض واقعی جایگزین شوند.",
        },
        "sites": [build_site_payload(s, cfg) for s in SITE_DEFS],
    }

    with open("dashboard_data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print("OK -> dashboard_data.json")
    for s in payload["sites"]:
        print(s["site_id"], s["kpis"])


if __name__ == "__main__":
    main()
