# -*- coding: utf-8 -*-
"""
ml_models.py
-------------
دو مدل یادگیری ماشین که هسته تحلیلی داشبورد را تشکیل می‌دهند:

۱) baseline_forecast_model
   یک مدل رگرسیونی (RandomForest) که یاد می‌گیرد «مصرف مورد انتظار» یک سایت در هر
   ساعت چقدر است (بر اساس ساعت روز، روز هفته، ماه و دما). این همان رویکرد Option-C
   در پروتکل IPMVP برای تعیین خط مبنا (baseline) است — به‌جای زیرمتر کردن هر
   تجهیز، از یک مدل رگرسیونی روی کل سایت استفاده می‌کنیم. تفاوت «واقعی منهای
   مورد انتظار» هم مبنای شناسایی اتلاف/فرصت صرفه‌جویی است و هم — وقتی بعداً
   اقدام اصلاحی انجام شد — مبنای اثبات صرفه‌جویی به مشتری (M&V).

۲) detect_anomalies
   ترکیبی از IsolationForest (برای الگوهای غیرعادی چندمتغیره) + آستانه‌های
   قطعی حوزه‌ای (مثلاً PF زیر ۰٫۸۵ یا افت ولتاژ بیش از ۵٪) برای پرچم‌گذاری
   رخدادهایی که باید در پنل هشدار داشبورد نمایش داده شوند.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor, IsolationForest
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error


FEATURE_COLS = ["hour", "day_of_week", "month", "is_weekend", "temperature"]


def train_baseline_forecast_model(df: pd.DataFrame):
    """آموزش مدل خط مبنا روی کل تاریخچه سایت و بازگرداندن مدل + خطای اعتبارسنجی."""
    X = df[FEATURE_COLS]
    y = df["active_power_kw"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, shuffle=True
    )

    model = RandomForestRegressor(
        n_estimators=200, max_depth=10, min_samples_leaf=5, random_state=42, n_jobs=-1
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    mape = float(np.mean(np.abs((y_test - y_pred) / np.maximum(y_test, 1e-6))) * 100)

    return model, {"mae_kw": round(float(mae), 2), "mape_percent": round(mape, 2)}


def add_expected_load(df: pd.DataFrame, model) -> pd.DataFrame:
    df = df.copy()
    df["expected_power_kw"] = model.predict(df[FEATURE_COLS])
    df["load_gap_kw"] = df["active_power_kw"] - df["expected_power_kw"]
    return df


def detect_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """
    برگرداندن ردیف‌های ناهنجار با نوع و شدت. دو منبع تشخیص:
      - قواعد قطعی حوزه‌ای (آستانه‌های مهندسی برق)
      - IsolationForest روی (load_gap, power_factor, thd, voltage) برای الگوهای ترکیبی غیرعادی
    """
    df = df.copy()
    feats = df[["load_gap_kw", "power_factor", "thd_percent", "voltage_pu"]].fillna(0)

    iso = IsolationForest(contamination=0.015, random_state=42, n_jobs=-1)
    df["iso_flag"] = iso.fit_predict(feats) == -1

    records = []
    for _, row in df.iterrows():
        reasons = []
        severity = 0
        if row["power_factor"] < 0.80:
            reasons.append("افت شدید ضریب قدرت")
            severity = max(severity, 3)
        elif row["power_factor"] < 0.85:
            reasons.append("افت ضریب قدرت")
            severity = max(severity, 2)
        if row["voltage_pu"] < 0.95 or row["voltage_pu"] > 1.05:
            reasons.append("انحراف ولتاژ از بازه مجاز")
            severity = max(severity, 2)
        if row["thd_percent"] > 8:
            reasons.append("اعوجاج هارمونیکی بالا (THD)")
            severity = max(severity, 2)
        if row["load_gap_kw"] > 3 * df["load_gap_kw"].std():
            reasons.append("مصرف به‌طور غیرمنتظره بالاتر از الگوی معمول")
            severity = max(severity, 2)
        if row["iso_flag"] and not reasons:
            reasons.append("الگوی ترکیبی غیرعادی (چند پارامتر هم‌زمان)")
            severity = max(severity, 1)

        if reasons:
            records.append({
                "timestamp": row["timestamp"].isoformat(),
                "site_id": row["site_id"],
                "reasons": reasons,
                "severity": severity,
                "power_factor": round(float(row["power_factor"]), 3),
                "voltage_pu": round(float(row["voltage_pu"]), 3),
                "thd_percent": round(float(row["thd_percent"]), 2),
                "active_power_kw": round(float(row["active_power_kw"]), 1),
            })

    anomalies = pd.DataFrame(records)
    if len(anomalies):
        anomalies = anomalies.sort_values("severity", ascending=False)
    return anomalies
