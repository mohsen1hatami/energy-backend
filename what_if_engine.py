# -*- coding: utf-8 -*-
"""
what_if_engine.py
-------------------
شبیه‌سازی سناریوهای «اگر...آنگاه» برای برآورد صرفه‌جویی سالانه، با استفاده
از همان تابع صورتحساب tariff_engine.monthly_bill روی نسخه‌ی اصلاح‌شده داده.
این خروجی مستقیماً در شبیه‌ساز داشبورد استفاده می‌شود.
"""

import numpy as np
import pandas as pd
from tariff_engine import TariffConfig, monthly_bill


def scenario_pf_correction(df: pd.DataFrame, target_pf: float = 0.95) -> pd.DataFrame:
    """فرض نصب/اصلاح بانک خازنی: ضریب قدرت را به سطح هدف می‌رساند."""
    out = df.copy()
    out["power_factor"] = np.maximum(out["power_factor"], target_pf)
    return out


def scenario_peak_shift(df: pd.DataFrame, shift_fraction: float = 0.20) -> pd.DataFrame:
    """
    فرض جابه‌جایی بخشی از بار قابل‌انعطاف از ساعات اوج‌بار (۱۹-۲۳) به کم‌باری (۲۳-۷).
    فقط برای بار صنعتی/فرآیندی که واقعاً قابل زمان‌بندی مجدد است منطقی است.
    """
    out = df.copy()
    peak_mask = (out["hour"] >= 19) & (out["hour"] < 23)
    shifted_kw = out.loc[peak_mask, "active_power_kw"] * shift_fraction
    out.loc[peak_mask, "active_power_kw"] -= shifted_kw

    # توزیع بار جابه‌جاشده در ساعات کم‌باری همان روز (به‌طور مساوی بین ساعات ۲۳-۷)
    total_shifted_per_day = shifted_kw.groupby(out.loc[peak_mask, "timestamp"].dt.date).sum()
    off_mask = (out["hour"] >= 23) | (out["hour"] < 7)
    for day, amount in total_shifted_per_day.items():
        day_off_mask = off_mask & (out["timestamp"].dt.date == day)
        n = day_off_mask.sum()
        if n > 0:
            out.loc[day_off_mask, "active_power_kw"] += amount / n
    return out


def run_what_if_scenarios(df: pd.DataFrame, cfg: TariffConfig) -> dict:
    baseline_bill = monthly_bill(df, cfg)["total_bill_rial"].sum()
    months_covered = df["timestamp"].dt.to_period("M").nunique()
    annualize = 12 / max(months_covered, 1)

    results = {}
    for name, scenario_df in [
        ("pf_correction", scenario_pf_correction(df)),
        ("peak_shift", scenario_peak_shift(df)),
    ]:
        scenario_bill = monthly_bill(scenario_df, cfg)["total_bill_rial"].sum()
        savings_rial = baseline_bill - scenario_bill
        results[name] = {
            "period_savings_rial": round(float(savings_rial), 0),
            "annualized_savings_rial": round(float(savings_rial * annualize), 0),
            "savings_percent": round(float(savings_rial / baseline_bill * 100), 2),
        }

    # سناریوی ترکیبی (هر دو اقدام هم‌زمان)
    combined_df = scenario_peak_shift(scenario_pf_correction(df))
    combined_bill = monthly_bill(combined_df, cfg)["total_bill_rial"].sum()
    combined_savings = baseline_bill - combined_bill
    results["combined"] = {
        "period_savings_rial": round(float(combined_savings), 0),
        "annualized_savings_rial": round(float(combined_savings * annualize), 0),
        "savings_percent": round(float(combined_savings / baseline_bill * 100), 2),
    }

    results["baseline_annualized_bill_rial"] = round(float(baseline_bill * annualize), 0)
    return results
