"""
Plain-language insights, generated deterministically (no LLM) in EN / AR / TR.

Reads the profile + data and writes the kind of sentences an analyst would open
with: the trend, the Pareto 80/20, the biggest category, an unusual spike, a
correlation, and a data-quality nudge. Fully offline.
"""
from __future__ import annotations

from . import analyze
from .profiler import columns_by_type, parse_number

# --- localisation -----------------------------------------------------------
T = {
    "summary": {
        "en": "{rows} records across {cols} columns, ready to explore.",
        "ar": "{rows} سجلًا عبر {cols} عمودًا، جاهزة للاستكشاف.",
        "tr": "{cols} sütunda {rows} kayıt, keşfe hazır.",
    },
    "trend_up": {
        "en": "{measure} is trending up — about {pct}% higher by {end} ({sv} → {ev}).",
        "ar": "{measure} في ارتفاع — أعلى بنحو {pct}% بحلول {end} ({sv} → {ev}).",
        "tr": "{measure} yükselişte — {end} itibarıyla yaklaşık %{pct} daha yüksek ({sv} → {ev}).",
    },
    "trend_down": {
        "en": "{measure} is trending down — about {pct}% lower by {end} ({sv} → {ev}).",
        "ar": "{measure} في انخفاض — أقل بنحو {pct}% بحلول {end} ({sv} → {ev}).",
        "tr": "{measure} düşüşte — {end} itibarıyla yaklaşık %{pct} daha düşük ({sv} → {ev}).",
    },
    "trend_flat": {
        "en": "{measure} has been broadly stable over the period.",
        "ar": "{measure} مستقر إلى حد كبير خلال الفترة.",
        "tr": "{measure} dönem boyunca büyük ölçüde sabit kaldı.",
    },
    "peak": {
        "en": "It peaked at {pv} around {pl}.",
        "ar": "بلغ ذروته عند {pv} قرب {pl}.",
        "tr": "En yüksek {pv} değerine {pl} civarında ulaştı.",
    },
    "pareto": {
        "en": "The top {k} {dim} account for {share}% of {measure} — a classic 80/20.",
        "ar": "أعلى {k} من {dim} تمثل {share}% من {measure} — قاعدة 80/20.",
        "tr": "İlk {k} {dim}, {measure} değerinin %{share}'ini oluşturuyor — klasik 80/20.",
    },
    "top_cat": {
        "en": "{name} leads {dim} with {val} ({share}% of the total).",
        "ar": "{name} يتصدر {dim} بـ {val} ({share}% من الإجمالي).",
        "tr": "{name}, {dim} içinde {val} ile başı çekiyor (toplamın %{share}'i).",
    },
    "outlier": {
        "en": "Unusual {dir} in {measure} around {label} — worth a look.",
        "ar": "{dir} غير معتاد في {measure} قرب {label} — يستحق المراجعة.",
        "tr": "{label} civarında {measure} değerinde olağandışı {dir} — incelemeye değer.",
    },
    "corr": {
        "en": "{a} and {b} move together (correlation {r}).",
        "ar": "{a} و {b} يتحركان معًا (ارتباط {r}).",
        "tr": "{a} ve {b} birlikte hareket ediyor (korelasyon {r}).",
    },
    "missing": {
        "en": "Heads up: {pct}% of {column} is missing.",
        "ar": "تنبيه: {pct}% من {column} مفقودة.",
        "tr": "Dikkat: {column} sütununun %{pct}'i eksik.",
    },
}
_DIR = {
    "spike": {"en": "spike", "ar": "قفزة", "tr": "sıçrama"},
    "drop": {"en": "drop", "ar": "هبوط", "tr": "düşüş"},
}


def _t(key, lang):
    return T[key].get(lang, T[key]["en"])


def _fmt(x):
    if x is None:
        return "—"
    ax = abs(x)
    if ax >= 1_000_000:
        return f"{x/1_000_000:.1f}M"
    if ax >= 1_000:
        return f"{x:,.0f}"
    if x == int(x):
        return f"{int(x)}"
    return f"{x:.1f}"


def generate(prof, columns, rows, lang="en"):
    if lang not in ("en", "ar", "tr"):
        lang = "en"
    out = []
    by = columns_by_type(prof)
    n = prof["n_rows"]
    out.append({"icon": "database", "kind": "summary",
                "text": _t("summary", lang).format(rows=f"{n:,}", cols=len(prof["columns"]))})

    measures = [c for c in by["number"]]
    primary = measures[0] if measures else None
    dates = by["date"]
    cats = [c for c in by["category"] if 2 <= c.get("n_unique", 0) <= 30]

    # trend
    if dates and primary:
        spec = {"type": "line", "dim": dates[0]["name"], "measure": primary["name"],
                "agg": "sum"}
        cd = analyze.chart_data(prof, columns, rows, spec)
        data, labels = cd.get("datasets", [{}])[0].get("data", []), cd.get("labels", [])
        if len(data) >= 2:
            sv, ev = data[0], data[-1]
            if sv:
                pct = round(100 * (ev - sv) / abs(sv))
            else:
                # first period is exactly 0 — a jump to a non-zero value is a real
                # (strong) move, not "stable". Report direction with a large magnitude.
                pct = 100 if ev > 0 else (-100 if ev < 0 else 0)
            if abs(pct) >= 8:
                key = "trend_up" if pct > 0 else "trend_down"
                out.append({"icon": "trending-up" if pct > 0 else "trending-down",
                            "kind": "trend",
                            "text": _t(key, lang).format(measure=primary["name"], pct=abs(pct),
                                                         end=labels[-1], sv=_fmt(sv), ev=_fmt(ev))})
            else:
                out.append({"icon": "arrow-right", "kind": "trend",
                            "text": _t("trend_flat", lang).format(measure=primary["name"])})
            pk = max(range(len(data)), key=lambda i: data[i])
            out.append({"icon": "flame", "kind": "peak",
                        "text": _t("peak", lang).format(pv=_fmt(data[pk]), pl=labels[pk])})
            an = cd.get("anomalies") or []
            if an:
                a = an[0]
                mean = sum(data) / len(data)
                d = "spike" if a["value"] > mean else "drop"
                out.append({"icon": "alert-triangle", "kind": "outlier",
                            "text": _t("outlier", lang).format(
                                dir=_DIR[d][lang], measure=primary["name"], label=labels[a["index"]])})

    # pareto + top category
    if cats and primary:
        c = cats[0]
        spec = {"type": "bar", "dim": c["name"], "measure": primary["name"], "agg": "sum",
                "limit": 1000}
        cd = analyze.chart_data(prof, columns, rows, spec)
        pairs = list(zip(cd.get("labels", []), cd.get("datasets", [{}])[0].get("data", [])))
        pairs = [(l, v) for l, v in pairs if l != "Other"]
        total = sum(v for _, v in pairs) or 1
        pairs.sort(key=lambda kv: -kv[1])
        if pairs:
            top = pairs[0]
            out.append({"icon": "crown", "kind": "top_cat",
                        "text": _t("top_cat", lang).format(name=top[0], dim=c["name"],
                                                           val=_fmt(top[1]),
                                                           share=round(100 * top[1] / total))})
            if len(pairs) >= 4:
                k = min(3, len(pairs))
                share = round(100 * sum(v for _, v in pairs[:k]) / total)
                if share >= 55:
                    out.append({"icon": "chart-pie", "kind": "pareto",
                                "text": _t("pareto", lang).format(k=k, dim=c["name"],
                                                                  share=share, measure=primary["name"])})

    # correlation
    corr = analyze.correlations(prof, columns, rows, top=1)
    if corr:
        c0 = corr[0]
        out.append({"icon": "link", "kind": "corr",
                    "text": _t("corr", lang).format(a=c0["a"], b=c0["b"], r=c0["r"])})

    # worst missing column
    worst = max(prof["columns"], key=lambda c: c["n_missing"], default=None)
    if worst and n and worst["n_missing"] / n >= 0.1:
        out.append({"icon": "alert-circle", "kind": "missing",
                    "text": _t("missing", lang).format(
                        pct=round(100 * worst["n_missing"] / n), column=worst["name"])})

    return out[:7]
