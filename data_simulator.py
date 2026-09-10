# -*- coding: utf-8 -*-
"""
data_simulator.py
------------------
تولید داده مصنوعی (Synthetic) ساعتی مصرف برق و پارامترهای کیفیت توان برای دو
مشترک نمونه: یک واحد صنعتی (کارخانه) و یک ساختمان تجاری/اداری.

هدف این ماژول فقط شبیه‌سازی برای دمو/توسعه است. در نسخه واقعی محصول، این ورودی
باید از OCR/API قبض برق + کنتور هوشمند/آنالایزر کیفیت توان مشترک واقعی خوانده شود.
"""

import numpy as np
import pandas as pd


def _seasonal_temperature(day_of_year: np.ndarray) -> np.ndarray:
    """دمای روزانه فرضی تهران با یک موج سینوسی سالانه + نویز (برای مدل‌سازی بار سرمایشی)."""
    base = 18 + 14 * np.sin(2 * np.pi * (day_of_year - 100) / 365)
    noise = np.random.normal(0, 2.0, size=len(day_of_year))
    return base + noise


def generate_site_data(
    site_id: str,
    profile: str,
    start_date: str = "2026-03-01",
    n_days: int = 180,
    contract_demand_kw: float = 500.0,
    seed: int = 42,
) -> pd.DataFrame:
    """
    تولید سری زمانی ساعتی برای یک سایت.

    profile: 'industrial' یا 'commercial'
        industrial -> بار پایه بالا و نسبتاً ثابت در شیفت‌های کاری، افت ضریب قدرت
                      به‌خاطر موتور/درایو، هارمونیک بالاتر
        commercial -> بار به‌شدت وابسته به دما (سرمایش/گرمایش)، افت شدید در تعطیلات
    """
    rng = np.random.default_rng(seed)
    np.random.seed(seed)

    n_hours = n_days * 24
    timestamps = pd.date_range(start=start_date, periods=n_hours, freq="h")
    df = pd.DataFrame({"timestamp": timestamps})

    df["hour"] = df["timestamp"].dt.hour
    df["day_of_week"] = df["timestamp"].dt.dayofweek  # 0=Mon ... 6=Sun
    df["day_of_year"] = df["timestamp"].dt.dayofyear
    df["month"] = df["timestamp"].dt.month
    # تعطیلات آخر هفته ایران: پنجشنبه نیمه‌تعطیل (3)، جمعه تعطیل (4) در dayofweek پایتون
    df["is_weekend"] = df["day_of_week"].isin([4]).astype(int)
    df["is_half_day"] = df["day_of_week"].isin([3]).astype(int)

    df["temperature"] = _seasonal_temperature(df["day_of_year"].values)

    if profile == "industrial":
        # سه شیفت کاری با افت مصرف در شیفت شب و توقف کامل جمعه‌ها
        shift_factor = np.where(
            (df["hour"] >= 7) & (df["hour"] < 15), 1.00,
            np.where((df["hour"] >= 15) & (df["hour"] < 23), 0.85, 0.55)
        )
        weekend_factor = np.where(df["is_weekend"] == 1, 0.15,
                                   np.where(df["is_half_day"] == 1, 0.6, 1.0))
        base_load = 420 * shift_factor * weekend_factor
        cooling_load = np.maximum(df["temperature"] - 24, 0) * 3.5  # خنک‌کاری تجهیزات/سالن
        noise = rng.normal(0, 12, size=n_hours)
        active_power_kw = np.clip(base_load + cooling_load + noise, 10, None)

        # افت تدریجی ضریب قدرت از روز ۹۰ به بعد (فرض: پیرشدن بانک خازنی/افزایش بار موتوری)
        pf_drift = np.where(df["day_of_year"] > df["day_of_year"].min() + 90,
                             0.10 * (df["day_of_year"] - (df["day_of_year"].min() + 90)) / 90, 0)
        power_factor = np.clip(0.93 - pf_drift + rng.normal(0, 0.01, n_hours), 0.62, 0.97)
        thd_percent = np.clip(4.5 + (0.93 - power_factor) * 30 + rng.normal(0, 0.4, n_hours), 2, 15)
        voltage_pu = 1.0 + rng.normal(0, 0.012, n_hours)  # ولتاژ بر حسب per-unit نسبت به نامی

    elif profile == "commercial":
        occ_factor = np.where(
            (df["hour"] >= 8) & (df["hour"] < 21), 1.0,
            np.where((df["hour"] >= 21) & (df["hour"] < 23), 0.5, 0.20)
        )
        weekend_factor = np.where(df["is_weekend"] == 1, 0.35, 1.0)
        base_load = 90 * occ_factor * weekend_factor
        hvac_load = (np.maximum(df["temperature"] - 22, 0) ** 1.15) * 4.2 * occ_factor
        heating_load = (np.maximum(10 - df["temperature"], 0)) * 2.1 * occ_factor
        noise = rng.normal(0, 6, size=n_hours)
        active_power_kw = np.clip(base_load + hvac_load + heating_load + noise, 5, None)

        power_factor = np.clip(0.90 + rng.normal(0, 0.02, n_hours), 0.70, 0.98)
        thd_percent = np.clip(3.0 + rng.normal(0, 0.5, n_hours), 1.5, 9)
        voltage_pu = 1.0 + rng.normal(0, 0.010, n_hours)

    else:
        raise ValueError("profile باید 'industrial' یا 'commercial' باشد")

    # --- تزریق چند رخداد ناهنجاری واقعی برای این‌که موتور تشخیص ناهنجاری چیزی برای پیدا کردن داشته باشد
    anomaly_days = rng.choice(np.arange(10, n_days - 5), size=5, replace=False)
    for d in anomaly_days:
        mask = (df["day_of_year"] == df["day_of_year"].min() + d)
        kind = rng.choice(["demand_spike", "pf_drop", "voltage_sag"])
        if kind == "demand_spike":
            active_power_kw[mask] *= rng.uniform(1.35, 1.6)
        elif kind == "pf_drop":
            power_factor[mask] = np.clip(power_factor[mask] - rng.uniform(0.15, 0.25), 0.5, None)
        elif kind == "voltage_sag":
            voltage_pu[mask] -= rng.uniform(0.06, 0.10)

    df["active_power_kw"] = active_power_kw
    df["power_factor"] = power_factor
    df["thd_percent"] = thd_percent
    df["voltage_pu"] = voltage_pu
    df["reactive_power_kvar"] = active_power_kw * np.tan(np.arccos(np.clip(power_factor, 0.5, 0.999)))
    df["site_id"] = site_id
    df["profile"] = profile
    df["contract_demand_kw"] = contract_demand_kw

    return df


if __name__ == "__main__":
    industrial = generate_site_data("site_A", "industrial", contract_demand_kw=550)
    commercial = generate_site_data("site_B", "commercial", contract_demand_kw=220)
    print(industrial.head())
    print(commercial.head())
