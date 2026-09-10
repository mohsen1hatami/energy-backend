# -*- coding: utf-8 -*-
"""
tariff_engine.py
-----------------
موتور محاسبه هزینه برق بر مبنای ساختار تعرفه مشترکین صنعتی/تولیدی ایران:
    - انرژی سه‌نرخی: کم‌باری / میان‌باری / اوج‌بار
    - کارمزد قدرت (دیماند) بر مبنای بیشینه توان مصرفی ثبت‌شده در بازه صورتحساب
    - جریمه ضریب قدرت پایین‌تر از حد مجاز (معمولاً ۰٫۹)

⚠️ نکته مهم برای استفاده واقعی:
نرخ پایه انرژی (۱۴,۴۲۰ ریال/kWh) و ضرایب ساعتی (کم‌باری×۰٫۵ / میان‌باری×۱ / اوج‌بار×۲)
بر اساس آخرین نرخ‌نامه اعلامی وزارت نیرو برای مشترکان تولید (صنعت و معدن) در سال ۱۴۰۵ است
و صرفاً جنبه‌ی نمایشی/تخمینی دارد. نرخ کارمزد قدرت (دیماند) و فرمول دقیق جریمه ضریب قدرت
در این نسخه پارامترهای *فرضی و قابل‌تنظیم* هستند — پیش از استفاده واقعی، این اعداد را
حتماً از روی قبض واقعی مشترک یا شرکت توزیع/برق منطقه‌ای محلی به‌روزرسانی کنید (دقیقاً همان
کاری که ماژول OCR قبض برق در محصول نهایی باید انجام دهد).
"""

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class TariffConfig:
    base_rate_rial_per_kwh: float = 14420.0     # نرخ پایه انرژی (میان‌باری = ضریب ۱)
    off_peak_multiplier: float = 0.5
    mid_peak_multiplier: float = 1.0
    peak_multiplier: float = 2.0
    demand_charge_rial_per_kw: float = 2_200_000.0   # PLACEHOLDER — از قبض واقعی جایگزین شود
    pf_target: float = 0.90
    pf_penalty_factor: float = 0.5              # شدت جریمه به ازای هر واحد افت PF از حد مجاز


def tariff_period(hour: int) -> str:
    """تعیین بازه زمانی تعرفه بر اساس ساعت شبانه‌روز (تقریبی — به فصل و قرارداد بستگی دارد)."""
    if 23 <= hour or hour < 7:
        return "off_peak"
    if 19 <= hour < 23:
        return "peak"
    return "mid_peak"


def apply_tariff(df: pd.DataFrame, cfg: TariffConfig = TariffConfig()) -> pd.DataFrame:
    df = df.copy()
    df["tariff_period"] = df["hour"].apply(tariff_period)

    multiplier_map = {
        "off_peak": cfg.off_peak_multiplier,
        "mid_peak": cfg.mid_peak_multiplier,
        "peak": cfg.peak_multiplier,
    }
    df["rate_rial_per_kwh"] = df["tariff_period"].map(multiplier_map) * cfg.base_rate_rial_per_kwh
    df["energy_cost_rial"] = df["active_power_kw"] * df["rate_rial_per_kwh"]  # هر ردیف = ۱ ساعت -> kWh = kW

    # جریمه ضریب قدرت ساعتی (ساده‌شده): فقط وقتی PF زیر حد مجاز است اعمال می‌شود
    pf_shortfall = np.maximum(cfg.pf_target - df["power_factor"], 0)
    df["pf_penalty_rial"] = df["energy_cost_rial"] * pf_shortfall * cfg.pf_penalty_factor / cfg.pf_target

    return df


def monthly_bill(df: pd.DataFrame, cfg: TariffConfig = TariffConfig()) -> pd.DataFrame:
    """تجمیع هزینه به تفکیک ماه، شامل هزینه انرژی، جریمه ضریب قدرت و کارمزد قدرت (دیماند)."""
    df = apply_tariff(df, cfg)
    df["year_month"] = df["timestamp"].dt.to_period("M").astype(str)

    grouped = df.groupby("year_month").agg(
        energy_cost_rial=("energy_cost_rial", "sum"),
        pf_penalty_rial=("pf_penalty_rial", "sum"),
        peak_demand_kw=("active_power_kw", "max"),
        avg_power_factor=("power_factor", "mean"),
        total_kwh=("active_power_kw", "sum"),
    ).reset_index()

    grouped["demand_charge_rial"] = grouped["peak_demand_kw"] * cfg.demand_charge_rial_per_kw
    grouped["total_bill_rial"] = (
        grouped["energy_cost_rial"] + grouped["pf_penalty_rial"] + grouped["demand_charge_rial"]
    )
    return grouped


# ---------------------------------------------------------------------------
# تعرفه «سایر مصارف» / خانگی — پلکانی + سورشارژ اوج‌بار + تخفیف کم‌باری
# ---------------------------------------------------------------------------
# این بخش از روی یک قبض واقعی (سایر مصارف، شرکت توزیع برق خراسان شمالی)
# استخراج و صحت‌سنجی شد: مجموع اقلام محاسبه‌شده با «مبلغ صورتحساب دوره» ی
# واقعی دقیقاً برابر بود. برخلاف مدل صنعتی بالا (دیماند + ضریب قدرت)، این
# مشترکین دیماند/ضریب‌قدرت ندارند.
from dataclasses import dataclass, field


@dataclass
class GeneralTariffTier:
    from_kwh: float
    to_kwh: float  # None برای پله آخر (نامحدود)
    rate_rial_per_kwh: float


def _default_tiers():
    # نرخ‌های واقعی استخراج‌شده از قبض نمونه (تیر ۱۴۰۴). ایران این پله‌ها را
    # معمولاً هرساله/هر فصل به‌روزرسانی می‌کند — با ورود قبض‌های جدیدتر باید
    # این جدول را از روی آخرین قبض واقعی هر مشترک بازسازی/به‌روز کرد
    # (calibrate_general_tariff_from_bill این کار را خودکار انجام می‌دهد).
    return [
        GeneralTariffTier(0, 100, 7630),
        GeneralTariffTier(100, 200, 7916),
        GeneralTariffTier(200, 300, 8297),
        GeneralTariffTier(300, 400, 8583),
        GeneralTariffTier(400, 500, 9537),
        GeneralTariffTier(500, 600, 11158),
        GeneralTariffTier(600, None, 12684),
    ]


@dataclass
class GeneralTariffConfig:
    tiers_30d: list = field(default_factory=_default_tiers)
    peak_surcharge_rate_rial_per_kwh: float = 7630.0
    off_peak_discount_rate_rial_per_kwh: float = -3815.0
    # اقلام تقریباً ثابت (مستقل از الگوی مصرف) — از روی آخرین قبض واقعی
    # کالیبره می‌شوند؛ چون از یک قبض تنها نمی‌توان فرمول دقیق‌شان را استخراج
    # کرد (ممکن است به آمپراژ/قدرت قراردادی وابسته باشند، نه به مصرف).
    seasonal_peak_charge_rial: float = 0.0
    subscription_fee_rial_per_30d: float = 0.0
    electricity_duty_rial: float = 0.0
    insurance_fee_rial: float = 0.0
    power_plant_fuel_charge_rial: float = 0.0
    vat_rate: float = 0.09  # نرخ فعلی مالیات بر ارزش‌افزوده ایران؛ دوره‌به‌دوره از دولت بررسی شود


def apply_block_tariff(kwh_30d_normalized: float, tiers: list) -> float:
    """اعمال تعرفه پلکانی روی مصرف نرمال‌شده ۳۰ روزه؛ خروجی: مبلغ ۳۰ روزه (ریال)."""
    remaining = kwh_30d_normalized
    total = 0.0
    for tier in tiers:
        span = (tier.to_kwh - tier.from_kwh) if tier.to_kwh is not None else remaining
        used = max(0.0, min(remaining, span))
        total += used * tier.rate_rial_per_kwh
        remaining -= used
        if remaining <= 0:
            break
    return total


def calculate_general_tou_bill(
    total_kwh: float,
    peak_kwh: float,
    off_peak_kwh: float,
    days_covered: float,
    cfg: GeneralTariffConfig = None,
    previous_balance_rial: float = 0.0,
    rounding_adjustment_rial: float = 0.0,
) -> dict:
    """محاسبه صورتحساب برای تعرفه «سایر مصارف» — دقیقاً همان فرمول واقعی
    شرکت توزیع: نرمال‌سازی مصرف به ۳۰ روز -> اعمال پلکان -> نسبت‌گیری به
    تعداد روز واقعی -> بعلاوه سورشارژ اوج‌بار / تخفیف کم‌باری -> بعلاوه
    اقلام ثابت -> مالیات -> بدهکاری قبلی.

    این تابع همان تابعی است که شبیه‌ساز «اگر/آنگاه» باید صدا بزند: کافی است
    total_kwh/peak_kwh/off_peak_kwh فرضی (بعد از یک اقدام صرفه‌جویی) به آن
    داده شود تا صورتحساب واقع‌بینانه (با احتساب غیرخطی‌بودن پله‌ها) محاسبه شود.
    """
    cfg = cfg or GeneralTariffConfig()
    kwh_30d = total_kwh * 30.0 / days_covered
    energy_30d = apply_block_tariff(kwh_30d, cfg.tiers_30d)
    energy_charge_rial = energy_30d / 30.0 * days_covered

    peak_surcharge_rial = peak_kwh * cfg.peak_surcharge_rate_rial_per_kwh
    off_peak_discount_rial = off_peak_kwh * cfg.off_peak_discount_rate_rial_per_kwh

    subtotal = (
        energy_charge_rial + peak_surcharge_rial + off_peak_discount_rial
        + cfg.seasonal_peak_charge_rial
        + cfg.subscription_fee_rial_per_30d / 30.0 * days_covered
        + cfg.electricity_duty_rial + cfg.insurance_fee_rial + cfg.power_plant_fuel_charge_rial
    )
    vat_rial = subtotal * cfg.vat_rate
    amount_payable_rial = subtotal + vat_rial + previous_balance_rial + rounding_adjustment_rial

    return {
        "kwh_30d_normalized": round(kwh_30d, 2),
        "energy_charge_rial": round(energy_charge_rial),
        "peak_surcharge_rial": round(peak_surcharge_rial),
        "off_peak_discount_rial": round(off_peak_discount_rial),
        "subtotal_before_vat_rial": round(subtotal),
        "vat_rial": round(vat_rial),
        "amount_payable_rial": round(amount_payable_rial),
    }


def calibrate_general_tariff_from_bill(parsed_bill: dict) -> GeneralTariffConfig:
    """ساخت GeneralTariffConfig مستقیماً از خروجی bill_parser.parse_bill روی
    یک قبض واقعی — یعنی با هر قبض جدید، موتور خودکار به‌روز می‌شود.

    نکته: استخراج دقیق «تک‌تک پله‌ها» از این قالب PDF به‌دلیل جابه‌جایی ستون‌ها
    در سطرهای مختلف (کوئرک pdftotext روی این تمپلیت Crystal Reports) گاهی
    ناقص می‌آید. برای همین، پله‌های استخراج‌شده را با «مبلغ مصرف نرمال‌شده
    ۳۰ روزه» صحت‌سنجی می‌کنیم؛ اگر جور در نیامد یا تعداد پله‌ها کم بود، به
    جدول پیش‌فرض (که از همین قبض به‌صورت دستی صحت‌سنجی شده) برمی‌گردیم.
    """
    extracted = [
        GeneralTariffTier(t["from_kwh"], t["to_kwh"], t["rate_rial_per_kwh"])
        for t in parsed_bill.get("tariff_tiers_30d", [])
    ]
    expected_30d_amount = parsed_bill.get("energy_charge_30d_normalized_rial")
    extracted_sum = sum(
        (t.to_kwh - t.from_kwh if t.to_kwh else 0) * t.rate_rial_per_kwh for t in extracted
    )
    tiers_ok = (
        len(extracted) >= 5
        and expected_30d_amount
        and abs(extracted_sum - expected_30d_amount) / expected_30d_amount < 0.01
    )
    tiers = extracted if tiers_ok else _default_tiers()

    vat = parsed_bill.get("vat_rial") or 0
    subtotal = parsed_bill.get("subtotal_before_vat_rial") or 1
    vat_rate = vat / subtotal if subtotal else 0.09

    peak = parsed_bill.get("peak_load_surcharge") or {}
    offpeak = parsed_bill.get("off_peak_discount") or {}

    return GeneralTariffConfig(
        tiers_30d=tiers,
        peak_surcharge_rate_rial_per_kwh=peak.get("rate_rial_per_kwh", 0) or 0,
        off_peak_discount_rate_rial_per_kwh=offpeak.get("rate_rial_per_kwh", 0) or 0,
        seasonal_peak_charge_rial=parsed_bill.get("seasonal_peak_charge_rial") or 0,
        subscription_fee_rial_per_30d=(parsed_bill.get("subscription_fee_rial") or 0)
        / max(parsed_bill.get("days_covered") or 30, 1) * 30,
        electricity_duty_rial=parsed_bill.get("electricity_duty_rial") or 0,
        insurance_fee_rial=parsed_bill.get("insurance_fee_rial") or 0,
        power_plant_fuel_charge_rial=parsed_bill.get("power_plant_fuel_charge_rial") or 0,
        vat_rate=vat_rate,
    )
