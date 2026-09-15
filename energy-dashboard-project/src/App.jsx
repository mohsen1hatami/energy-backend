import { useState, useMemo } from "react";
import {
  AreaChart,
  Area,
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import {
  Zap,
  Gauge,
  Activity,
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  SlidersHorizontal,
  Layers,
  Info,
} from "lucide-react";

// ---------------------------------------------------------------------------
// این خروجی واقعیِ build_dashboard_data.py است (نه داده نمایشی). همان فایلی که
// پایپ‌لاین پایتون شما (data_simulator -> ml_models -> tariff_engine ->
// what_if_engine) تولید کرده، مستقیماً اینجا جاسازی شده تا داشبورد از خروجی
// واقعی مدل‌ها بخواند، نه از آرایه‌های ساختگی.
// ---------------------------------------------------------------------------
const PF_TARGET_DEFAULT = 0.9; // مقدار مرجع پیش‌فرض؛ برای هر مشترک واقعی از site.kpis گرفته می‌شود

const palette = {
  bg: "#12161c",
  panel: "#1a1f27",
  panelAlt: "#171c23",
  border: "#262d37",
  text: "#e7ebef",
  muted: "#8a94a3",
  accent: "#e0a64d",
  accentDim: "rgba(224,166,77,0.16)",
  danger: "#e2583f",
  good: "#5fb489",
  info: "#5b9bc9",
  grid: "#232a33",
};

const fontSans =
  "'Segoe UI', system-ui, -apple-system, 'Vazirmatn', Tahoma, sans-serif";
const fontMono = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

// ---------------------------------------------------------------------------
// کمک‌تابع‌های قالب‌بندی فارسی
// ---------------------------------------------------------------------------
function faNum(n, opts = {}) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("fa-IR", opts);
}

function faDate(iso, opts = { month: "short", day: "numeric" }) {
  try {
    return new Date(iso).toLocaleDateString("fa-IR", opts);
  } catch {
    return iso;
  }
}

function faMonthLabel(yearMonth) {
  try {
    return new Date(`${yearMonth}-15T00:00:00`).toLocaleDateString("fa-IR", {
      month: "long",
    });
  } catch {
    return yearMonth;
  }
}

function billionRial(rial) {
  return `${faNum(rial / 1e9, { maximumFractionDigits: 2 })} میلیارد ریال`;
}

function millionRial(rial) {
  return `${faNum(rial / 1e6, { maximumFractionDigits: 0 })} م. ریال`;
}

function severityMeta(sev) {
  if (sev >= 3) return { color: palette.danger, label: "شدید" };
  if (sev === 2) return { color: palette.accent, label: "متوسط" };
  return { color: palette.info, label: "خفیف" };
}

const RANGE_OPTIONS = [
  { key: "7d", label: "۷ روز", days: 7 },
  { key: "30d", label: "۳۰ روز", days: 30 },
  { key: "season", label: "کل بازه (۶ ماه)", days: null },
];

const SCENARIO_META = {
  pf_correction: {
    label: "اصلاح ضریب قدرت",
    hint: "نصب/تنظیم بانک خازنی تا رسیدن PF به ۰٫۹۵",
  },
  peak_shift: {
    label: "انتقال بار پیک",
    hint: "جابه‌جایی ۲۰٪ بار قابل‌انعطاف از ساعت اوج به کم‌باری",
  },
  combined: {
    label: "ترکیبی",
    hint: "هر دو اقدام هم‌زمان",
  },
};

// آدرس زنده بک‌اند FastAPI (Render). با هر قبض تازه، همین آدرس صدا زده می‌شود.
const API_BASE = "https://energy-backend-n9oe.onrender.com";

function BillUploadPanel() {
  const [file, setFile] = useState(null);
  const [peakReduction, setPeakReduction] = useState(10);
  const [shiftPercent, setShiftPercent] = useState(30);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!file) {
      setError("اول یک فایل PDF قبض انتخاب کنید.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("peak_reduction_percent", String(peakReduction));
      form.append("shift_to_offpeak_percent", String(shiftPercent));
      const res = await fetch(`${API_BASE}/api/tariff/estimate`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `خطای سرور (${res.status})`);
      }
      setResult(await res.json());
    } catch (err) {
      setError(
        err.message === "Failed to fetch"
          ? "اتصال به سرور برقرار نشد. اگر چند دقیقه از آخرین استفاده گذشته، سرور رایگان خوابیده — یک بار دیگر امتحان کنید (بیدار شدنش ۳۰-۶۰ ثانیه طول می‌کشد)."
          : err.message
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <Panel
      title="آپلود قبض واقعی خودتان"
      subtitle="تحلیل زنده روی سرور — فایل شما ذخیره نمی‌شود"
      icon={Zap}
      style={{ marginBottom: 16 }}
    >
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
          style={{
            color: palette.muted,
            fontSize: 13,
            fontFamily: fontSans,
          }}
        />

        <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
          <label style={{ fontSize: 12, color: palette.muted, flex: "1 1 200px" }}>
            حذف مصرف اوج‌بار: {faNum(peakReduction)}٪
            <input
              type="range"
              min={0}
              max={50}
              value={peakReduction}
              onChange={(e) => setPeakReduction(Number(e.target.value))}
              style={{ width: "100%", marginTop: 6 }}
            />
          </label>
          <label style={{ fontSize: 12, color: palette.muted, flex: "1 1 200px" }}>
            انتقال باقی‌مانده اوج به کم‌باری: {faNum(shiftPercent)}٪
            <input
              type="range"
              min={0}
              max={100}
              value={shiftPercent}
              onChange={(e) => setShiftPercent(Number(e.target.value))}
              style={{ width: "100%", marginTop: 6 }}
            />
          </label>
        </div>

        <button
          type="submit"
          disabled={loading}
          style={{
            background: loading ? palette.border : palette.accent,
            color: loading ? palette.muted : "#1a1508",
            border: "none",
            borderRadius: 8,
            padding: "10px 16px",
            fontSize: 13,
            fontWeight: 600,
            fontFamily: fontSans,
            cursor: loading ? "default" : "pointer",
            alignSelf: "flex-start",
          }}
        >
          {loading ? "در حال تحلیل… (ممکن است تا ۶۰ ثانیه طول بکشد)" : "تحلیل کن"}
        </button>
      </form>

      {error && (
        <div style={{ marginTop: 14, fontSize: 12, color: palette.danger }}>{error}</div>
      )}

      {result && (
        <div
          style={{
            marginTop: 16,
            paddingTop: 14,
            borderTop: `1px solid ${palette.border}`,
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          <div style={{ fontSize: 13 }}>
            {result.subscriber_name} · {result.tariff_title}
          </div>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 11, color: palette.muted }}>صورتحساب فعلی</div>
              <div style={{ fontSize: 18, fontWeight: 700, fontFamily: fontMono }}>
                {millionRial(result.baseline.amount_payable_rial)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: palette.muted }}>با این اقدامات</div>
              <div style={{ fontSize: 18, fontWeight: 700, fontFamily: fontMono, color: palette.good }}>
                {millionRial(result.scenario.amount_payable_rial)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: palette.muted }}>صرفه‌جویی برآوردی</div>
              <div style={{ fontSize: 18, fontWeight: 700, fontFamily: fontMono, color: palette.good }}>
                {faNum(result.estimated_savings_percent, { maximumFractionDigits: 1 })}٪
              </div>
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}

function BulkBillsPanel() {
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!files.length) {
      setError("حداقل یک فایل PDF قبض انتخاب کنید (می‌توانید چند فایل را هم‌زمان انتخاب کنید).");
      return;
    }
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      const res = await fetch(`${API_BASE}/api/bills/batch`, { method: "POST", body: form });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `خطای سرور (${res.status})`);
      }
      setData(await res.json());
    } catch (err) {
      setError(
        err.message === "Failed to fetch"
          ? "اتصال برقرار نشد — اگر سرور مدتی بی‌کار بوده، یک بار دیگر امتحان کنید."
          : err.message
      );
    } finally {
      setLoading(false);
    }
  }

  const chartData = useMemo(() => {
    if (!data?.bills?.length) return [];
    return data.bills.map((b, i) => ({
      label: b.issue_date_jalali || `دوره ${i + 1}`,
      "مصرف (kWh)": b.total_consumption_kwh || 0,
      "قابل پرداخت (م.ریال)": Math.round((b.amount_payable_rial || 0) / 1e6),
    }));
  }, [data]);

  return (
    <Panel
      title="آپلود چند قبض برای تحلیل روند"
      subtitle="چند فایل PDF از ماه‌های مختلف را با هم انتخاب کنید — روند مصرف و هزینه در طول زمان ساخته می‌شود"
      icon={Layers}
      style={{ marginBottom: 16 }}
    >
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <input
          type="file"
          accept="application/pdf"
          multiple
          onChange={(e) => setFiles(Array.from(e.target.files || []))}
          style={{ color: palette.muted, fontSize: 13, fontFamily: fontSans }}
        />
        {files.length > 0 && (
          <div style={{ fontSize: 12, color: palette.muted }}>
            {faNum(files.length)} فایل انتخاب شد
          </div>
        )}
        <button
          type="submit"
          disabled={loading}
          style={{
            background: loading ? palette.border : palette.accent,
            color: loading ? palette.muted : "#1a1508",
            border: "none",
            borderRadius: 8,
            padding: "10px 16px",
            fontSize: 13,
            fontWeight: 600,
            fontFamily: fontSans,
            cursor: loading ? "default" : "pointer",
            alignSelf: "flex-start",
          }}
        >
          {loading ? "در حال پردازش همه فایل‌ها…" : "تحلیل کن"}
        </button>
      </form>

      {error && <div style={{ marginTop: 14, fontSize: 12, color: palette.danger }}>{error}</div>}

      {data && (
        <div style={{ marginTop: 16 }}>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap", marginBottom: 14 }}>
            <div>
              <div style={{ fontSize: 11, color: palette.muted }}>تعداد دوره تشخیص‌داده‌شده</div>
              <div style={{ fontSize: 18, fontWeight: 700, fontFamily: fontMono }}>
                {faNum(data.periods_count)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: palette.muted }}>مجموع مصرف</div>
              <div style={{ fontSize: 18, fontWeight: 700, fontFamily: fontMono }}>
                {faNum(data.summary.total_consumption_kwh)} kWh
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: palette.muted }}>میانگین صورتحساب ماهانه</div>
              <div style={{ fontSize: 18, fontWeight: 700, fontFamily: fontMono }}>
                {millionRial(data.summary.avg_monthly_payable_rial)}
              </div>
            </div>
          </div>

          {chartData.length > 1 && (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={chartData}>
                <CartesianGrid stroke={palette.grid} vertical={false} />
                <XAxis dataKey="label" stroke={palette.muted} fontSize={10} />
                <YAxis stroke={palette.muted} fontSize={10} />
                <Tooltip {...chartTooltipStyle} />
                <Bar dataKey="قابل پرداخت (م.ریال)" fill={palette.accent} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}

          {data.errors?.length > 0 && (
            <div style={{ marginTop: 12, fontSize: 12, color: palette.danger }}>
              {data.errors.length} فایل با خطا مواجه شد:{" "}
              {data.errors.map((e) => e.filename).join("، ")}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}


function RealSiteUploadPanel({ onSiteReady }) {
  const [pqFile, setPqFile] = useState(null);
  const [billFile, setBillFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!pqFile) {
      setError("فایل کیفیت توان (CSV یا Excel) الزامی است.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("pq_file", pqFile);
      if (billFile) form.append("bill_file", billFile);
      const res = await fetch(`${API_BASE}/api/site/build`, { method: "POST", body: form });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `خطای سرور (${res.status})`);
      }
      onSiteReady(await res.json());
    } catch (err) {
      setError(
        err.message === "Failed to fetch"
          ? "اتصال برقرار نشد — اگر سرور مدتی بی‌کار بوده، یک بار دیگر امتحان کنید."
          : err.message
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <Panel
      title="افزودن مشترک واقعی به داشبورد"
      subtitle="فایل کیفیت توان الزامی است؛ قبض اختیاری است ولی هزینه و سناریوهای صرفه‌جویی را هم فعال می‌کند"
      icon={Zap}
      style={{ marginBottom: 16 }}
    >
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div>
          <div style={{ fontSize: 12, color: palette.muted, marginBottom: 6 }}>فایل کیفیت توان (CSV/Excel) — الزامی</div>
          <input
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={(e) => setPqFile(e.target.files?.[0] || null)}
            style={{ color: palette.muted, fontSize: 13, fontFamily: fontSans }}
          />
        </div>
        <div>
          <div style={{ fontSize: 12, color: palette.muted, marginBottom: 6 }}>قبض PDF — اختیاری</div>
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => setBillFile(e.target.files?.[0] || null)}
            style={{ color: palette.muted, fontSize: 13, fontFamily: fontSans }}
          />
        </div>
        <button
          type="submit"
          disabled={loading}
          style={{
            background: loading ? palette.border : palette.accent,
            color: loading ? palette.muted : "#1a1508",
            border: "none",
            borderRadius: 8,
            padding: "10px 16px",
            fontSize: 13,
            fontWeight: 600,
            fontFamily: fontSans,
            cursor: loading ? "default" : "pointer",
            alignSelf: "flex-start",
          }}
        >
          {loading ? "در حال ساخت داشبورد… (ممکن است تا ۶۰ ثانیه طول بکشد)" : "بساز و در داشبورد نمایش بده"}
        </button>
      </form>
      {error && <div style={{ marginTop: 14, fontSize: 12, color: palette.danger }}>{error}</div>}
    </Panel>
  );
}

function Panel({ title, subtitle, icon: Icon, children, style }) {
  return (
    <div
      style={{
        background: palette.panel,
        border: `1px solid ${palette.border}`,
        borderRadius: 10,
        padding: 18,
        ...style,
      }}
    >
      {title && (
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            marginBottom: 14,
            gap: 12,
          }}
        >
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: palette.text }}>
              {title}
            </div>
            {subtitle && (
              <div style={{ fontSize: 12, color: palette.muted, marginTop: 2 }}>
                {subtitle}
              </div>
            )}
          </div>
          {Icon && <Icon size={18} color={palette.muted} style={{ flexShrink: 0 }} />}
        </div>
      )}
      {children}
    </div>
  );
}

const chartTooltipStyle = {
  contentStyle: {
    background: palette.panelAlt,
    border: `1px solid ${palette.border}`,
    fontSize: 12,
    fontFamily: fontSans,
  },
  labelStyle: { color: palette.text },
};

export default function EnergyManagementDashboard() {
  const [sites, setSites] = useState([]);
  const [siteId, setSiteId] = useState(null);
  const [rangeKey, setRangeKey] = useState("30d");
  const [scenarioKey, setScenarioKey] = useState("combined");

  const site = useMemo(
    () => sites.find((s) => s.site_id === siteId) || sites[0] || null,
    [sites, siteId]
  );
  const range = RANGE_OPTIONS.find((r) => r.key === rangeKey);
  const pfTarget = PF_TARGET_DEFAULT;

  function handleSiteReady(newSite) {
    setSites((prev) => [...prev.filter((s) => s.site_id !== newSite.site_id), newSite]);
    setSiteId(newSite.site_id);
  }

  const dailyFiltered = useMemo(() => {
    if (!site) return [];
    const series = site.daily_series || [];
    const sliced = range.days ? series.slice(-range.days) : series;
    return sliced.map((d) => ({
      ...d,
      label: faDate(d.timestamp),
    }));
  }, [site, range]);

  const demandWallData = useMemo(() => {
    if (!site) return [];
    return (site.hourly_profile_sample || []).map((h) => ({
      ...h,
      label: faNum(h.hour),
    }));
  }, [site]);

  const monthlyCostData = useMemo(() => {
    if (!site) return [];
    return (site.monthly_bills || []).map((m) => ({
      label: faMonthLabel(m.year_month),
      "هزینه انرژی": Math.round((m.energy_cost_rial || 0) / 1e6),
      "کارمزد دیماند": Math.round((m.demand_charge_rial || 0) / 1e6),
      "جریمه ضریب قدرت": Math.round((m.pf_penalty_rial || 0) / 1e6),
    }));
  }, [site]);

  // مقایسه ماه اخیر با ماه قبل (از monthly_bills واقعی، نه فرمول ساختگی)
  const billTrend = useMemo(() => {
    if (!site) return null;
    const bills = site.monthly_bills || [];
    if (bills.length < 2) return null;
    const last = bills[bills.length - 1];
    const prev = bills[bills.length - 2];
    const pct = ((last.total_bill_rial - prev.total_bill_rial) / prev.total_bill_rial) * 100;
    return { value: last.total_bill_rial, pct };
  }, [site]);

  // مقایسه مصرف ۳۰ روز اخیر با ۳۰ روز قبل از آن (از daily_series واقعی)
  const consumptionTrend = useMemo(() => {
    if (!site) return { value: 0, pct: 0 };
    const series = site.daily_series || [];
    const last30 = series.slice(-30);
    const prev30 = series.slice(-60, -30);
    const sum = (arr) => arr.reduce((a, d) => a + d.actual_kwh, 0);
    const lastSum = sum(last30);
    const prevSum = sum(prev30);
    const pct = prevSum ? ((lastSum - prevSum) / prevSum) * 100 : 0;
    return { value: lastSum, pct };
  }, [site]);

  const demandPercent =
    site && site.kpis.contract_demand_kw
      ? (site.kpis.peak_demand_kw / site.kpis.contract_demand_kw) * 100
      : null;
  const pfOk = site && site.kpis.avg_power_factor != null ? site.kpis.avg_power_factor >= pfTarget : null;

  const kpis = !site
    ? []
    : [
        {
          label: "هزینه ماه اخیر",
          value: billTrend
            ? billionRial(billTrend.value)
            : site.kpis.total_bill_rial != null
            ? billionRial(site.kpis.total_bill_rial)
            : "—",
          delta: billTrend
            ? `${billTrend.pct >= 0 ? "+" : ""}${faNum(billTrend.pct, { maximumFractionDigits: 1 })}٪ نسبت به ماه قبل`
            : site.kpis.total_bill_rial != null
            ? "—"
            : "قبض آپلود نشده",
          positive: billTrend ? billTrend.pct <= 0 : true,
          icon: Zap,
        },
        {
          label: "مصرف اخیر",
          value: `${faNum(Math.round(consumptionTrend.value))} kWh`,
          delta: `${consumptionTrend.pct >= 0 ? "+" : ""}${faNum(consumptionTrend.pct, { maximumFractionDigits: 1 })}٪ نسبت به دوره قبل`,
          positive: consumptionTrend.pct <= 0,
          icon: Activity,
        },
        {
          label: "دیماند اوج",
          value:
            demandPercent != null
              ? `${faNum(site.kpis.peak_demand_kw)} از ${faNum(site.kpis.contract_demand_kw)} kW`
              : `${faNum(site.kpis.peak_demand_kw)} kW`,
          delta: demandPercent != null ? `${faNum(demandPercent, { maximumFractionDigits: 0 })}٪ ظرفیت قرارداد` : "ظرفیت قراردادی نامشخص",
          positive: demandPercent != null ? demandPercent < 90 : true,
          icon: Gauge,
        },
        {
          label: "میانگین ضریب قدرت",
          value:
            site.kpis.avg_power_factor != null
              ? faNum(site.kpis.avg_power_factor, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
              : "—",
          delta: pfOk == null ? "—" : pfOk ? "بالای حد مجاز تعرفه" : "زیر حد مجاز — در معرض جریمه",
          positive: pfOk == null ? true : pfOk,
          icon: pfOk === false ? TrendingDown : TrendingUp,
        },
      ];

  const scenario = site?.what_if ? site.what_if[scenarioKey] : null;

  return (
    <div
      dir="rtl"
      style={{
        minHeight: "100vh",
        background: palette.bg,
        color: palette.text,
        fontFamily: fontSans,
        padding: 24,
      }}
    >
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
          marginBottom: 20,
        }}
      >
        <div>
          <div style={{ fontSize: 20, fontWeight: 700 }}>پایش انرژی</div>
          <div style={{ fontSize: 13, color: palette.muted, marginTop: 2 }}>
            {site ? `${site.name} · ${faNum(site.kpis.days_covered)} روز داده` : "برای شروع، اطلاعات مشترک واقعی را آپلود کنید"}
          </div>
        </div>
        {sites.length > 0 && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select
              value={siteId || ""}
              onChange={(e) => setSiteId(e.target.value)}
              style={{
                background: palette.panel,
                border: `1px solid ${palette.border}`,
                color: palette.text,
                borderRadius: 8,
                padding: "8px 12px",
                fontSize: 13,
                fontFamily: fontSans,
              }}
            >
              {sites.map((s) => (
                <option key={s.site_id} value={s.site_id}>
                  {s.name}
                </option>
              ))}
            </select>
            {RANGE_OPTIONS.map((r) => {
              const active = rangeKey === r.key;
              return (
                <button
                  key={r.key}
                  onClick={() => setRangeKey(r.key)}
                  style={{
                    background: active ? palette.accentDim : "transparent",
                    border: `1px solid ${active ? palette.accent : palette.border}`,
                    color: active ? palette.accent : palette.muted,
                    borderRadius: 8,
                    padding: "8px 12px",
                    fontSize: 13,
                    fontFamily: fontSans,
                    cursor: "pointer",
                  }}
                >
                  {r.label}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <RealSiteUploadPanel onSiteReady={handleSiteReady} />

      {!site && (
        <Panel title="هنوز مشترک واقعی اضافه نشده" icon={Info} style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 13, color: palette.muted, lineHeight: 1.9 }}>
            فایل کیفیت توان (CSV/Excel) مشترک — و در صورت تمایل، قبض PDF برای هزینه و سناریوهای صرفه‌جویی — را در پنل بالا
            آپلود کنید. به‌محض ساخته‌شدن، همین‌جا با دیوار دیماند، روند مصرف، کیفیت توان و هشدارهای واقعی جایگزین این پیام می‌شود.
          </div>
        </Panel>
      )}

      {site && (
      <>
      <BillUploadPanel />
      <BulkBillsPanel />

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
          gap: 14,
          marginBottom: 16,
        }}
      >
        {kpis.map((k) => (
          <Panel key={k.label} style={{ padding: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <div style={{ fontSize: 12, color: palette.muted, marginBottom: 6 }}>{k.label}</div>
                <div style={{ fontSize: 20, fontWeight: 700, fontFamily: fontMono }}>{k.value}</div>
                <div
                  style={{
                    fontSize: 11,
                    color: k.positive ? palette.good : palette.danger,
                    marginTop: 4,
                  }}
                >
                  {k.delta}
                </div>
              </div>
              <k.icon size={18} color={palette.muted} />
            </div>
          </Panel>
        ))}
      </div>

      <Panel
        title="دیوار دیماند — پروفایل یک روز نمونه در برابر ظرفیت قرارداد"
        subtitle="کیلووات (kW) بر ساعت · خط نقطه‌چین: مصرف مورد انتظار طبق مدل RandomForest"
        icon={Gauge}
        style={{ marginBottom: 16 }}
      >
        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={demandWallData}>
            <CartesianGrid stroke={palette.grid} vertical={false} />
            <XAxis dataKey="label" stroke={palette.muted} fontSize={11} />
            <YAxis stroke={palette.muted} fontSize={11} />
            <Tooltip {...chartTooltipStyle} />
            {site.kpis.contract_demand_kw != null && (
              <ReferenceLine
                y={site.kpis.contract_demand_kw}
                stroke={palette.danger}
                strokeDasharray="4 4"
                label={{
                  value: `ظرفیت قرارداد ${faNum(site.kpis.contract_demand_kw)}`,
                  fill: palette.danger,
                  fontSize: 11,
                  position: "insideTopLeft",
                }}
              />
            )}
            <Area
              type="monotone"
              dataKey="active_power_kw"
              name="مصرف واقعی"
              stroke={palette.accent}
              fill={palette.accentDim}
              strokeWidth={2}
            />
            <Line
              type="monotone"
              dataKey="expected_power_kw"
              name="مورد انتظار (مدل)"
              stroke={palette.muted}
              strokeDasharray="4 3"
              dot={false}
              strokeWidth={1.5}
            />
          </AreaChart>
        </ResponsiveContainer>
      </Panel>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: 16,
          marginBottom: 16,
        }}
      >
        <Panel
          title="مصرف واقعی در برابر خط مبنا"
          subtitle={`kWh روزانه · خطای مدل (MAPE) ${faNum(site.kpis.model_mape_percent, { maximumFractionDigits: 1 })}٪`}
          icon={Activity}
        >
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={dailyFiltered}>
              <CartesianGrid stroke={palette.grid} vertical={false} />
              <XAxis dataKey="label" stroke={palette.muted} fontSize={10} minTickGap={24} />
              <YAxis stroke={palette.muted} fontSize={10} />
              <Tooltip {...chartTooltipStyle} />
              <Line
                type="monotone"
                dataKey="expected_kwh"
                stroke={palette.muted}
                strokeDasharray="4 3"
                dot={false}
                name="خط مبنا (مدل)"
              />
              <Line
                type="monotone"
                dataKey="actual_kwh"
                stroke={palette.accent}
                strokeWidth={2}
                dot={false}
                name="واقعی"
              />
            </LineChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="کیفیت توان" subtitle="ضریب قدرت و هارمونیک (THD) — میانگین روزانه" icon={TrendingDown}>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={dailyFiltered}>
              <CartesianGrid stroke={palette.grid} vertical={false} />
              <XAxis dataKey="label" stroke={palette.muted} fontSize={10} minTickGap={24} />
              <YAxis yAxisId="pf" domain={[0.75, 1]} stroke={palette.muted} fontSize={10} />
              <YAxis
                yAxisId="thd"
                orientation="right"
                domain={[0, 12]}
                stroke={palette.muted}
                fontSize={10}
              />
              <Tooltip {...chartTooltipStyle} />
              <ReferenceLine yAxisId="pf" y={pfTarget} stroke={palette.danger} strokeDasharray="3 3" />
              <Line
                yAxisId="pf"
                type="monotone"
                dataKey="avg_pf"
                stroke={palette.good}
                strokeWidth={2}
                dot={false}
                name="ضریب قدرت"
              />
              <Line
                yAxisId="thd"
                type="monotone"
                dataKey="avg_thd"
                stroke={palette.accent}
                strokeWidth={2}
                dot={false}
                name="THD %"
              />
            </LineChart>
          </ResponsiveContainer>
        </Panel>
      </div>

      {site.monthly_bills && site.monthly_bills.length > 0 && (
      <Panel
        title="ترکیب هزینه ماهانه صورت‌حساب"
        subtitle="میلیون ریال · انرژی + کارمزد دیماند + جریمه ضریب قدرت"
        icon={Layers}
        style={{ marginBottom: 16 }}
      >
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={monthlyCostData}>
            <CartesianGrid stroke={palette.grid} vertical={false} />
            <XAxis dataKey="label" stroke={palette.muted} fontSize={11} />
            <YAxis stroke={palette.muted} fontSize={11} />
            <Tooltip {...chartTooltipStyle} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="هزینه انرژی" stackId="s" fill={palette.accent} />
            <Bar dataKey="کارمزد دیماند" stackId="s" fill={palette.info} />
            <Bar dataKey="جریمه ضریب قدرت" stackId="s" fill={palette.danger} radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </Panel>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          gap: 16,
        }}
      >
        <Panel
          title="هشدارها"
          subtitle={`${faNum(site.kpis.anomaly_count)} رویداد در ${faNum(site.kpis.days_covered)} روز · ${site.anomalies.length} مورد با بالاترین شدت`}
          icon={AlertTriangle}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 10, maxHeight: 320, overflowY: "auto" }}>
            {site.anomalies.map((a, i) => {
              const meta = severityMeta(a.severity);
              return (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    gap: 10,
                    alignItems: "flex-start",
                    padding: "8px 10px",
                    borderRadius: 8,
                    background: palette.panelAlt,
                  }}
                >
                  <div
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 999,
                      background: meta.color,
                      marginTop: 5,
                      flexShrink: 0,
                    }}
                  />
                  <div>
                    <div style={{ fontSize: 13 }}>{a.reasons.join(" · ")}</div>
                    <div style={{ fontSize: 11, color: palette.muted, marginTop: 2 }}>
                      {faDate(a.timestamp, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                      {" · "}PF {faNum(a.power_factor, { maximumFractionDigits: 2 })}
                      {" · "}THD {faNum(a.thd_percent, { maximumFractionDigits: 1 })}٪
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </Panel>

        {site.what_if && scenario && (
        <Panel title="سناریوهای اگر / آنگاه" subtitle="خروجی واقعی what_if_engine.py — سالانه" icon={SlidersHorizontal}>
          <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
            {Object.keys(SCENARIO_META).map((key) => {
              const active = scenarioKey === key;
              return (
                <button
                  key={key}
                  onClick={() => setScenarioKey(key)}
                  style={{
                    background: active ? palette.accentDim : "transparent",
                    border: `1px solid ${active ? palette.accent : palette.border}`,
                    color: active ? palette.accent : palette.muted,
                    borderRadius: 8,
                    padding: "6px 10px",
                    fontSize: 12,
                    fontFamily: fontSans,
                    cursor: "pointer",
                  }}
                >
                  {SCENARIO_META[key].label}
                </button>
              );
            })}
          </div>

          <div style={{ fontSize: 12, color: palette.muted, marginBottom: 10 }}>
            {SCENARIO_META[scenarioKey].hint}
          </div>

          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 11, color: palette.muted, marginBottom: 4 }}>صرفه‌جویی سالانه برآوردی</div>
              <div style={{ fontSize: 20, fontWeight: 700, fontFamily: fontMono, color: palette.good }}>
                {millionRial(scenario.annualized_savings_rial)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: palette.muted, marginBottom: 4 }}>سهم از هزینه سالانه</div>
              <div style={{ fontSize: 20, fontWeight: 700, fontFamily: fontMono }}>
                {faNum(scenario.savings_percent, { maximumFractionDigits: 2 })}٪
              </div>
            </div>
          </div>

          <div
            style={{
              fontSize: 11,
              color: palette.muted,
              marginTop: 14,
              borderTop: `1px solid ${palette.border}`,
              paddingTop: 10,
              display: "flex",
              gap: 6,
              alignItems: "flex-start",
            }}
          >
            <Info size={13} style={{ flexShrink: 0, marginTop: 1 }} />
            <span>
              هزینه سالانه پایه (بدون اقدام): {billionRial(site.what_if.baseline_annualized_bill_rial)}. برای شبیه‌سازی زنده با
              پارامتر دلخواه (مثلاً PF هدف دلخواه)، مرحله بعد اتصال یک API زنده به همین ماژول پایتون است — نه فقط
              خواندن این عکس‌ فوری JSON.
            </span>
          </div>
        </Panel>
        )}
      </div>
      </>
      )}

      <div
        style={{
          fontSize: 11,
          color: palette.muted,
          marginTop: 16,
          padding: "10px 14px",
          background: palette.panelAlt,
          border: `1px solid ${palette.border}`,
          borderRadius: 8,
        }}
      >
        {site?.notes?.length ? site.notes.join(" · ") : "این بخش برای مشترک واقعی، یادداشت‌های کالیبراسیون تعرفه را نشان می‌دهد."}
      </div>
    </div>
  );
}
