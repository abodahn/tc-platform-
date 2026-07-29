"""
Traceability reports — DECLARATIONS ONLY, executed by app/services/reporting.py.

Imported from the bottom of app/routes/trace.py. Nothing here touches the database
at import time. Every report reads `trc_view`, the permission that opens the
module's own pages; the engine enforces it on the HTML page and on all three
export formats.

What is deliberately NOT here:
  * the full fibre-to-product CHAIN of one lot — that is a recursive walk
    (services.lot_chain) and a flat SELECT cannot express it. This register shows
    each lot with its IMMEDIATE parent, and /trace/lots/<id> shows the chain.
  * passport completeness — it is scored in Python over that same walk, so it
    cannot be aggregated honestly in SQL. It stays on /trace/passports.
  * the 60-day "expiring soon" badge — it needs date('now','+60 days'), which the
    PostgreSQL translation shim only rewrites when the offset is a bound
    parameter. The register is therefore sorted soonest-expiry first instead.
"""
from app.services import reporting as R

from .constants import MATERIAL_STANDARDS

_MOD = dict(module="trace", module_label="Traceability",
            module_label_ar="التتبع", module_label_tr="İzlenebilirlik",
            perm="trc_view")

# Derived certificate state, mirroring services.cert_status() minus its 'expiring'
# band (see the module docstring). date('now') is rewritten for PostgreSQL by
# app/db.py; anything else here is plain ANSI SQL.
_CERT_STATE = ("CASE WHEN COALESCE(c.status,'')='revoked' THEN 'Revoked' "
               "WHEN c.valid_until IS NULL OR c.valid_until='' THEN 'Unknown' "
               "WHEN c.valid_until < date('now') THEN 'Expired' "
               "WHEN c.valid_from IS NOT NULL AND c.valid_from > date('now') THEN 'Pending' "
               "ELSE 'Valid' END")

_TIER_OPTS = [("1", "Tier 1 — CMT", "المستوى 1 — التصنيع", "Kademe 1 — Konfeksiyon"),
              ("2", "Tier 2 — fabric mill", "المستوى 2 — مصنع الأقمشة",
               "Kademe 2 — Kumaş fabrikası"),
              ("3", "Tier 3 — spinner / dyehouse", "المستوى 3 — الغزل / الصباغة",
               "Kademe 3 — İplik / boyahane"),
              ("4", "Tier 4 — fibre origin", "المستوى 4 — مصدر الألياف",
               "Kademe 4 — Elyaf kaynağı")]


# --- 1. material lot register ---------------------------------------------
R.register(
    key="trace_lots", **_MOD,
    title="Material lot register",
    title_ar="سجل دفعات الخامات",
    title_tr="Malzeme parti kaydı",
    desc="Every material lot with its supplier, tier, fibre composition and the lot "
         "it was made from. A blank 'made from' is a chain that stops there.",
    desc_ar="كل دفعة خامة مع مورّدها ومستواها وتركيبها الليفي والدفعة المصنوعة منها. "
            "خانة فارغة في «مصنوعة من» تعني أن سلسلة التتبع تتوقف عندها.",
    desc_tr="Her malzeme partisi; tedarikçisi, kademesi, elyaf bileşimi ve yapıldığı parti "
            "ile. Boş bir 'yapıldığı parti' zincirin orada bittiği anlamına gelir.",
    select=("l.lot_ref AS lot_ref, l.material AS material, "
            "l.fibre_composition AS fibre, p.name AS supplier, p.tier AS tier, "
            "p.country AS supplier_country, COALESCE(l.qty,0) AS qty, l.uom AS uom, "
            "l.country_of_origin AS origin, l.received_date AS received, "
            "pl.lot_ref AS parent_ref"),
    frm=("trc_lots l LEFT JOIN trc_partners p ON p.id = l.partner_id "
         "LEFT JOIN trc_lots pl ON pl.id = l.parent_lot_id"),
    date_col="l.received_date",
    order="l.id DESC",
    columns=[
        R.col("lot_ref", "Lot ref", "رقم الدفعة", "Parti referansı"),
        R.col("material", "Material", "الخامة", "Malzeme"),
        R.col("fibre", "Fibre composition", "التركيب الليفي", "Elyaf bileşimi"),
        R.col("supplier", "Supplier", "المورّد", "Tedarikçi"),
        R.col("tier", "Tier", "المستوى", "Kademe", "num"),
        R.col("supplier_country", "Supplier country", "بلد المورّد", "Tedarikçi ülkesi"),
        R.col("qty", "Quantity", "الكمية", "Miktar", "num", total="SUM(COALESCE(l.qty,0))"),
        R.col("uom", "Unit", "الوحدة", "Birim"),
        R.col("origin", "Country of origin", "بلد المنشأ", "Menşe ülkesi"),
        R.col("received", "Received", "تاريخ الاستلام", "Giriş tarihi", "date"),
        R.col("parent_ref", "Made from lot", "مصنوعة من الدفعة", "Yapıldığı parti"),
    ],
    kpis=[
        R.kpi("lots", "Lots", "عدد الدفعات", "Parti sayısı", "COUNT(*)", better="none"),
        R.kpi("qty", "Quantity", "الكمية", "Miktar", "SUM(COALESCE(l.qty,0))",
              better="none"),
        R.kpi("linked", "With an upstream lot", "مرتبطة بدفعة سابقة", "Üst partiye bağlı",
              "SUM(CASE WHEN l.parent_lot_id IS NOT NULL THEN 1 ELSE 0 END)"),
        R.kpi("suppliers", "Suppliers", "عدد المورّدين", "Tedarikçi sayısı",
              "COUNT(DISTINCT l.partner_id)", better="none"),
    ],
    filters=[
        R.filt("material", "Material", "الخامة", "Malzeme", "l.material"),
        R.filt("supplier", "Supplier", "المورّد", "Tedarikçi", "p.name"),
        # CAST to text: the value always arrives as a string parameter, and
        # PostgreSQL will not compare an integer column with it.
        R.filt("tier", "Tier", "المستوى", "Kademe", "CAST(p.tier AS TEXT)", "select", "=",
               _TIER_OPTS),
        R.filt("origin", "Country of origin", "بلد المنشأ", "Menşe ülkesi",
               "l.country_of_origin"),
    ],
    chart=R.chart("bar", "COALESCE(NULLIF(p.name,''),'(no supplier)')",
                  "SUM(COALESCE(l.qty,0))", "COALESCE(NULLIF(p.name,''),'(no supplier)')",
                  "Quantity by supplier", "الكمية حسب المورّد",
                  "Tedarikçiye göre miktar"),
    icon="i-report",
)


# --- 2. material certificate expiry ---------------------------------------
R.register(
    key="trace_certs", **_MOD,
    title="Material certificate expiry",
    title_ar="انتهاء شهادات الخامات",
    title_tr="Malzeme sertifikası süreleri",
    desc="GOTS, OEKO-TEX, GRS and the rest, soonest expiry first. The state is "
         "derived from the dates, never from the stored column — a lapsed 'valid' lies.",
    desc_ar="شهادات GOTS و OEKO-TEX و GRS وغيرها، الأقرب انتهاءً أولًا. الحالة مشتقة من "
            "التواريخ وليست من الحقل المخزَّن — شهادة منتهية مكتوب عليها «سارية» تضلّل.",
    desc_tr="GOTS, OEKO-TEX, GRS ve diğerleri; süresi en yakın bitenden başlar. Durum "
            "saklanan alandan değil tarihlerden türetilir — süresi geçmiş bir 'geçerli' yanıltır.",
    select=("c.standard AS standard, c.cert_no AS cert_no, c.issuer AS issuer, "
            "p.name AS partner, l.lot_ref AS lot_ref, c.scope AS scope, "
            "c.valid_from AS valid_from, c.valid_until AS valid_until, "
            f"{_CERT_STATE} AS state, c.doc_ref AS doc_ref"),
    frm=("trc_certs c LEFT JOIN trc_partners p ON p.id = c.partner_id "
         "LEFT JOIN trc_lots l ON l.id = c.lot_id"),
    date_col="c.valid_until",
    order="COALESCE(NULLIF(c.valid_until,''),'9999-12-31') ASC, c.id ASC",
    columns=[
        R.col("standard", "Standard", "المعيار", "Standart"),
        R.col("cert_no", "Certificate no", "رقم الشهادة", "Sertifika no"),
        R.col("issuer", "Issuer", "الجهة المانحة", "Veren kurum"),
        # tr is "İş ortağı", the word app/static/i18n/tr.json already uses for
        # trc.field.partner — a report must not call it something else.
        R.col("partner", "Partner", "الشريك", "İş ortağı"),
        R.col("lot_ref", "Lot ref", "رقم الدفعة", "Parti referansı"),
        R.col("scope", "Scope", "النطاق", "Kapsam"),
        R.col("valid_from", "Valid from", "سارية من", "Geçerlilik başlangıcı", "date"),
        R.col("valid_until", "Valid until", "سارية حتى", "Geçerlilik bitişi", "date"),
        R.col("state", "State", "الحالة", "Durum"),
        R.col("doc_ref", "Document", "المستند", "Belge"),
    ],
    kpis=[
        R.kpi("certs", "Certificates", "عدد الشهادات", "Sertifika sayısı", "COUNT(*)",
              better="none"),
        R.kpi("valid", "Valid today", "سارية اليوم", "Bugün geçerli",
              f"SUM(CASE WHEN {_CERT_STATE}='Valid' THEN 1 ELSE 0 END)"),
        R.kpi("expired", "Expired", "منتهية", "Süresi dolmuş",
              f"SUM(CASE WHEN {_CERT_STATE}='Expired' THEN 1 ELSE 0 END)", better="down"),
        R.kpi("revoked", "Revoked", "ملغاة", "İptal edilmiş",
              f"SUM(CASE WHEN {_CERT_STATE}='Revoked' THEN 1 ELSE 0 END)", better="down"),
    ],
    filters=[
        R.filt("standard", "Standard", "المعيار", "Standart", "c.standard", "select", "=",
               [(s, s, s, s) for s in MATERIAL_STANDARDS]),
        R.filt("partner", "Partner", "الشريك", "İş ortağı", "p.name"),
        R.filt("state", "State", "الحالة", "Durum", _CERT_STATE, "select", "=",
               [("Valid", "Valid", "سارية", "Geçerli"),
                ("Expired", "Expired", "منتهية", "Süresi dolmuş"),
                ("Pending", "Not yet in force", "لم تبدأ بعد", "Henüz başlamamış"),
                ("Revoked", "Revoked", "ملغاة", "İptal edilmiş"),
                ("Unknown", "No expiry date", "بدون تاريخ انتهاء", "Bitiş tarihi yok")]),
    ],
    chart=R.chart("bar", "c.standard", "COUNT(*)", "c.standard",
                  "Certificates by standard", "الشهادات حسب المعيار",
                  "Standarda göre sertifikalar"),
    icon="i-report",
)


# --- 3. recorded ESG consumption ------------------------------------------
# RECORDED consumption divided by pieces. This is metered/invoiced data, NOT a
# certified LCA, and the description says so in all three languages.
R.register(
    key="trace_esg", **_MOD,
    title="Recorded energy, water and waste",
    title_ar="الطاقة والمياه والمخلفات المسجَّلة",
    title_tr="Kayıtlı enerji, su ve atık",
    desc="Metered and invoiced consumption per order or per month, with the per-garment "
         "intensity. Recorded data, not a certified footprint.",
    desc_ar="الاستهلاك المقيس والمفوتر لكل أمر أو لكل شهر، مع الكثافة لكل قطعة. بيانات "
            "مسجَّلة وليست بصمة كربونية معتمدة.",
    desc_tr="Sipariş veya ay bazında sayaç ve fatura tüketimi, parça başına yoğunlukla. "
            "Kayıtlı veridir, sertifikalı bir ayak izi değildir.",
    select=("COALESCE(e.label, e.period) AS scope, o.order_no AS order_no, "
            "e.period AS period, COALESCE(e.energy_kwh,0) AS energy_kwh, "
            "COALESCE(e.water_m3,0) AS water_m3, COALESCE(e.waste_kg,0) AS waste_kg, "
            "COALESCE(e.garments,0) AS garments, "
            "ROUND(CAST(COALESCE(e.energy_kwh,0)/NULLIF(e.garments,0) AS NUMERIC),3) "
            "  AS kwh_per_pc, "
            "ROUND(CAST(1000.0*COALESCE(e.water_m3,0)/NULLIF(e.garments,0) AS NUMERIC),2) "
            "  AS water_l_per_pc, "
            "e.source AS source, e.created_at AS created_at"),
    frm="trc_esg e LEFT JOIN ord_orders o ON o.id = e.order_id",
    date_col="e.created_at",
    order="e.id DESC",
    columns=[
        R.col("scope", "Scope", "النطاق", "Kapsam"),
        R.col("order_no", "Order", "الأمر", "Sipariş"),
        R.col("period", "Period", "الفترة", "Dönem"),
        R.col("energy_kwh", "Energy (kWh)", "الطاقة (ك.و.س)", "Enerji (kWh)", "num",
              total="SUM(COALESCE(e.energy_kwh,0))"),
        R.col("water_m3", "Water (m³)", "المياه (م³)", "Su (m³)", "num",
              total="SUM(COALESCE(e.water_m3,0))"),
        R.col("waste_kg", "Waste (kg)", "المخلفات (كجم)", "Atık (kg)", "num",
              total="SUM(COALESCE(e.waste_kg,0))"),
        R.col("garments", "Garments", "عدد القطع", "Parça", "num",
              total="SUM(COALESCE(e.garments,0))"),
        R.col("kwh_per_pc", "kWh / garment", "ك.و.س لكل قطعة", "Parça başına kWh", "num"),
        R.col("water_l_per_pc", "Water L / garment", "لتر لكل قطعة", "Parça başına litre",
              "num"),
        R.col("source", "Source", "المصدر", "Kaynak"),
        R.col("created_at", "Recorded", "تاريخ التسجيل", "Kayıt tarihi", "date"),
    ],
    kpis=[
        R.kpi("records", "Records", "عدد السجلات", "Kayıt sayısı", "COUNT(*)",
              better="none"),
        R.kpi("energy", "Energy (kWh)", "الطاقة (ك.و.س)", "Enerji (kWh)",
              "SUM(COALESCE(e.energy_kwh,0))", better="down"),
        R.kpi("water", "Water (m³)", "المياه (م³)", "Su (m³)",
              "SUM(COALESCE(e.water_m3,0))", better="down"),
        R.kpi("waste", "Waste (kg)", "المخلفات (كجم)", "Atık (kg)",
              "SUM(COALESCE(e.waste_kg,0))", better="down"),
        R.kpi("kwh_pc", "kWh per garment", "ك.و.س لكل قطعة", "Parça başına kWh",
              "ROUND(CAST(SUM(COALESCE(e.energy_kwh,0))/"
              "NULLIF(SUM(COALESCE(e.garments,0)),0) AS NUMERIC),3)", better="down"),
    ],
    filters=[
        R.filt("period", "Period", "الفترة", "Dönem", "e.period"),
        R.filt("order_no", "Order", "الأمر", "Sipariş", "o.order_no"),
        R.filt("source", "Source", "المصدر", "Kaynak", "e.source"),
    ],
    chart=R.chart("trend", "COALESCE(NULLIF(e.period,''),substr(e.created_at,1,7))",
                  "SUM(COALESCE(e.energy_kwh,0))",
                  "COALESCE(NULLIF(e.period,''),substr(e.created_at,1,7))",
                  "Energy by period", "الطاقة حسب الفترة", "Döneme göre enerji"),
    icon="i-report",
)
