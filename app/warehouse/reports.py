"""
Warehouse reports — DECLARATIONS ONLY, executed by app/services/reporting.py.

Imported from the bottom of app/routes/warehouse.py, so the specs are registered
when the blueprint is imported. NOTHING here touches the database at import time
(gunicorn runs --preload: a query here would run before the port is bound).

Every report reads `wh_view`, the same permission that opens the module's own
pages, and the engine enforces it identically on the HTML page and on the CSV,
XLSX and PDF exports.

Portability notes — this SQL runs on SQLite locally and PostgreSQL on Render:
  * no julianday()/AGE(): stock ageing is expressed as "oldest received first"
    plus the engine's date range, not as computed day buckets;
  * ROUND(x, n) needs a NUMERIC first argument on PostgreSQL, hence the CASTs;
  * NULLIF(...) guards every division, so an empty or zero denominator gives
    NULL (an empty cell), never a division-by-zero error;
  * ORDER BY never relies on the engine's NULL ordering, which differs between
    SQLite (NULLs first) and PostgreSQL (NULLs last) — COALESCE decides it.
"""
from app.services import reporting as R

from .constants import MATERIAL_KINDS, ROLL_STATUS

_MOD = dict(module="warehouse", module_label="Warehouse",
            module_label_ar="المستودع", module_label_tr="Depo",
            perm="wh_view")

# available = on hand - reserved. This module makes every decision on AVAILABILITY,
# never on raw stock, so the reports do too.
_AVAIL = "(COALESCE(m.stock_qty,0)-COALESCE(m.reserved_qty,0))"
_VALUE = "(COALESCE(m.stock_qty,0)*COALESCE(m.avg_cost,0))"
_SHORT = (f"CASE WHEN COALESCE(m.reorder_level,0)>0 AND {_AVAIL}<=m.reorder_level "
          "THEN 'yes' ELSE 'no' END")

_KIND_OPTS = [("fabric", "Fabric", "قماش", "Kumaş"),
              ("trim", "Trim", "إكسسوارات", "Aksesuar"),
              ("accessory", "Accessory", "ملحقات", "Yardımcı malzeme")]
assert {o[0] for o in _KIND_OPTS} == set(MATERIAL_KINDS)   # labels track the constant

_ROLL_OPTS = [("available", "Available", "متاح", "Mevcut"),
              ("partial", "Partly used", "مستخدم جزئيًا", "Kısmen kullanılmış"),
              ("consumed", "Consumed", "مستهلك", "Tüketilmiş"),
              ("quarantine", "Quarantine", "حجر", "Karantina")]
assert {o[0] for o in _ROLL_OPTS} == set(ROLL_STATUS)


# --- 1. stock on hand vs reserved -----------------------------------------
R.register(
    key="warehouse_stock", **_MOD,
    title="Stock on hand vs reserved",
    title_ar="المخزون المتاح مقابل المحجوز",
    title_tr="Eldeki stok ile rezerve stok",
    desc="On hand, committed to an order, and what is really free to cut — with the "
         "value sitting on the shelf. No date filter: stock is a snapshot of now.",
    desc_ar="المخزون الموجود، والمحجوز لأمر إنتاج، والمتاح فعليًا للقص — مع قيمة المخزون. "
            "لا يوجد فلتر تاريخ: المخزون لقطة للحظة الحالية.",
    desc_tr="Eldeki, bir siparişe bağlanmış ve gerçekten kesime hazır miktar — raftaki "
            "değeriyle birlikte. Tarih filtresi yok: stok anlık bir görüntüdür.",
    select=("m.code AS code, m.name AS name, m.kind AS kind, m.color AS color, "
            "m.uom AS uom, COALESCE(m.stock_qty,0) AS on_hand, "
            "COALESCE(m.reserved_qty,0) AS reserved, "
            f"{_AVAIL} AS available, COALESCE(m.reorder_level,0) AS reorder_level, "
            f"COALESCE(m.avg_cost,0) AS avg_cost, {_VALUE} AS stock_value, "
            "m.supplier AS supplier, m.warehouse AS warehouse"),
    frm="wh_materials m",
    base_where=["COALESCE(m.is_active,1)=1"],
    order=f"{_VALUE} DESC, m.code ASC",
    columns=[
        R.col("code", "Code", "الكود", "Kod"),
        R.col("name", "Material", "الخامة", "Malzeme"),
        R.col("kind", "Kind", "النوع", "Tür"),
        R.col("color", "Colour", "اللون", "Renk"),
        R.col("uom", "Unit", "الوحدة", "Birim"),
        R.col("on_hand", "On hand", "المخزون", "Eldeki", "num",
              total="SUM(COALESCE(m.stock_qty,0))"),
        R.col("reserved", "Reserved", "المحجوز", "Rezerve", "num",
              total="SUM(COALESCE(m.reserved_qty,0))"),
        R.col("available", "Available", "المتاح", "Kullanılabilir", "num",
              total=f"SUM({_AVAIL})"),
        R.col("reorder_level", "Reorder level", "حد إعادة الطلب", "Sipariş seviyesi", "num"),
        R.col("avg_cost", "Avg cost", "متوسط التكلفة", "Ortalama maliyet", "num"),
        R.col("stock_value", "Stock value", "قيمة المخزون", "Stok değeri", "num",
              total=f"SUM({_VALUE})"),
        R.col("supplier", "Supplier", "المورّد", "Tedarikçi"),
        R.col("warehouse", "Store", "المخزن", "Depo"),
    ],
    kpis=[
        R.kpi("materials", "Materials", "عدد الخامات", "Malzeme sayısı", "COUNT(*)",
              better="none"),
        R.kpi("stock_value", "Stock value", "قيمة المخزون", "Stok değeri", f"SUM({_VALUE})",
              better="none"),
        R.kpi("reserved", "Reserved", "المحجوز", "Rezerve", "SUM(COALESCE(m.reserved_qty,0))",
              better="none"),
        R.kpi("short", "Below reorder level", "تحت حد إعادة الطلب", "Sipariş seviyesi altında",
              f"SUM(CASE WHEN COALESCE(m.reorder_level,0)>0 AND {_AVAIL}<=m.reorder_level "
              "THEN 1 ELSE 0 END)", better="down"),
    ],
    filters=[
        R.filt("kind", "Kind", "النوع", "Tür", "m.kind", "select", "=", _KIND_OPTS),
        R.filt("code", "Code", "الكود", "Kod", "m.code"),
        R.filt("supplier", "Supplier", "المورّد", "Tedarikçi", "m.supplier"),
        R.filt("short", "Below reorder level", "تحت حد إعادة الطلب",
               "Sipariş seviyesi altında", _SHORT, "select", "=",
               [("yes", "Yes", "نعم", "Evet"), ("no", "No", "لا", "Hayır")]),
    ],
    chart=R.chart("pareto", "m.code", f"SUM({_VALUE})", "m.code",
                  "Stock value by material", "قيمة المخزون حسب الخامة",
                  "Malzemeye göre stok değeri"),
    icon="i-report",
)


# --- 2. rolls, shade lots and ageing --------------------------------------
_FREE = "(COALESCE(r.remaining_m,0)-COALESCE(r.reserved_m,0))"

R.register(
    key="warehouse_rolls", **_MOD,
    title="Fabric rolls, shade lots and ageing",
    title_ar="أثواب القماش ودفعات الصباغة والتقادم",
    title_tr="Kumaş topları, parti renkleri ve yaşlanma",
    desc="Every roll with the metres left, the metres committed and its dye lot — "
         "oldest received first, so the slowest-moving cloth is at the top.",
    desc_ar="كل ثوب مع الأمتار المتبقية والمحجوزة ودفعة الصباغة — الأقدم استلامًا أولًا، "
            "لتظهر الأقمشة الراكدة في الأعلى.",
    desc_tr="Her top; kalan metre, bağlanmış metre ve boya partisiyle — en eski girişten "
            "başlar, böylece en yavaş hareket eden kumaş en üstte olur.",
    select=("r.roll_no AS roll_no, m.code AS code, m.name AS material, "
            "r.shade_lot AS shade_lot, r.supplier_lot AS supplier_lot, r.grade AS grade, "
            "COALESCE(r.width_cm,0) AS width_cm, COALESCE(r.length_m,0) AS length_m, "
            "COALESCE(r.remaining_m,0) AS remaining_m, "
            f"COALESCE(r.reserved_m,0) AS reserved_m, {_FREE} AS free_m, "
            "r.status AS status, r.warehouse AS warehouse, r.bin AS bin, "
            "r.received_at AS received_at"),
    frm="wh_rolls r JOIN wh_materials m ON m.id = r.material_id",
    date_col="r.received_at",
    order="COALESCE(NULLIF(r.received_at,''),'9999-12-31') ASC, r.id ASC",
    columns=[
        R.col("roll_no", "Roll no", "رقم الثوب", "Top no"),
        R.col("code", "Code", "الكود", "Kod"),
        R.col("material", "Material", "الخامة", "Malzeme"),
        R.col("shade_lot", "Shade lot", "دفعة الصباغة", "Boya partisi"),
        R.col("supplier_lot", "Supplier lot", "دفعة المورّد", "Tedarikçi partisi"),
        R.col("grade", "Grade", "الدرجة", "Kalite"),
        R.col("width_cm", "Width (cm)", "العرض (سم)", "En (cm)", "num"),
        R.col("length_m", "Received (m)", "المستلم (م)", "Giriş (m)", "num",
              total="SUM(COALESCE(r.length_m,0))"),
        R.col("remaining_m", "Remaining (m)", "المتبقي (م)", "Kalan (m)", "num",
              total="SUM(COALESCE(r.remaining_m,0))"),
        R.col("reserved_m", "Reserved (m)", "المحجوز (م)", "Rezerve (m)", "num",
              total="SUM(COALESCE(r.reserved_m,0))"),
        R.col("free_m", "Free (m)", "المتاح (م)", "Serbest (m)", "num",
              total=f"SUM({_FREE})"),
        R.col("status", "Status", "الحالة", "Durum"),
        R.col("warehouse", "Store", "المخزن", "Depo"),
        R.col("bin", "Bin", "الموقع", "Raf"),
        R.col("received_at", "Received", "تاريخ الاستلام", "Giriş tarihi", "date"),
    ],
    kpis=[
        R.kpi("rolls", "Rolls", "عدد الأثواب", "Top sayısı", "COUNT(*)", better="none"),
        R.kpi("remaining", "Metres on hand", "الأمتار المتاحة", "Eldeki metre",
              "SUM(COALESCE(r.remaining_m,0))", better="none"),
        R.kpi("free", "Metres free", "الأمتار غير المحجوزة", "Serbest metre",
              f"SUM({_FREE})", better="none"),
        R.kpi("lots", "Shade lots", "دفعات الصباغة", "Boya partisi",
              "COUNT(DISTINCT r.shade_lot)", better="none"),
        R.kpi("quarantine", "In quarantine", "في الحجر", "Karantinada",
              "SUM(CASE WHEN r.status='quarantine' THEN 1 ELSE 0 END)", better="down"),
    ],
    filters=[
        R.filt("code", "Code", "الكود", "Kod", "m.code"),
        R.filt("shade_lot", "Shade lot", "دفعة الصباغة", "Boya partisi", "r.shade_lot"),
        R.filt("status", "Status", "الحالة", "Durum", "r.status", "select", "=", _ROLL_OPTS),
        R.filt("store", "Store", "المخزن", "Depo", "r.warehouse"),
    ],
    chart=R.chart("bar", "COALESCE(r.shade_lot,'(no lot)')",
                  "SUM(COALESCE(r.remaining_m,0))", "COALESCE(r.shade_lot,'(no lot)')",
                  "Metres on hand by shade lot", "الأمتار المتاحة حسب دفعة الصباغة",
                  "Boya partisine göre eldeki metre"),
    icon="i-report",
)


# --- 3. consumption and slow movers ---------------------------------------
# LEFT JOIN, so a material that has NEVER been issued still produces a row — that
# row IS the slow mover, and an INNER JOIN would hide exactly the ones being
# looked for. For the same reason this report has NO date column: a date filter
# lands in the WHERE clause, which turns the LEFT JOIN back into an inner join
# and silently drops every never-issued material.
R.register(
    key="warehouse_slow", **_MOD,
    title="Consumption and slow movers",
    title_ar="الاستهلاك والأصناف الراكدة",
    title_tr="Tüketim ve yavaş hareket edenler",
    desc="What each material has consumed over its life and when it last moved. "
         "Sorted oldest movement first: the top of this list is money asleep on a shelf.",
    desc_ar="ما استهلكته كل خامة خلال عمرها وآخر حركة لها. مرتبة من الأقدم حركة: أعلى "
            "القائمة أموال راكدة على الرف.",
    desc_tr="Her malzemenin ömrü boyunca tükettiği miktar ve son hareket tarihi. En eski "
            "hareketten başlar: listenin başı rafta uyuyan paradır.",
    select=("m.code AS code, m.name AS name, m.kind AS kind, m.uom AS uom, "
            "COALESCE(SUM(-v.qty),0) AS issued_qty, "
            "COALESCE(SUM(-v.qty*COALESCE(v.unit_cost,0)),0) AS issued_value, "
            "MAX(v.created_at) AS last_issue, "
            "COALESCE(m.stock_qty,0) AS on_hand, "
            "COALESCE(m.stock_qty,0)*COALESCE(m.avg_cost,0) AS stock_value"),
    frm=("wh_materials m LEFT JOIN wh_movements v ON v.material_id = m.id "
         "AND v.qty < 0 AND v.type IN ('issue','transfer')"),
    group=("m.id, m.code, m.name, m.kind, m.uom, m.stock_qty, m.avg_cost"),
    base_where=["COALESCE(m.is_active,1)=1"],
    order=("COALESCE(MAX(v.created_at),'0000-00-00') ASC, "
           "COALESCE(m.stock_qty,0)*COALESCE(m.avg_cost,0) DESC"),
    columns=[
        R.col("code", "Code", "الكود", "Kod"),
        R.col("name", "Material", "الخامة", "Malzeme"),
        R.col("kind", "Kind", "النوع", "Tür"),
        R.col("uom", "Unit", "الوحدة", "Birim"),
        R.col("last_issue", "Last issued", "آخر صرف", "Son çıkış", "date"),
        R.col("issued_qty", "Issued (life)", "المصروف (كامل العمر)", "Çıkan (toplam)", "num",
              total="SUM(issued_qty)"),
        R.col("issued_value", "Issued value", "قيمة المصروف", "Çıkan değer", "num",
              total="SUM(issued_value)"),
        R.col("on_hand", "On hand", "المخزون", "Eldeki", "num", total="SUM(on_hand)"),
        R.col("stock_value", "Stock value", "قيمة المخزون", "Stok değeri", "num",
              total="SUM(stock_value)"),
    ],
    kpis=[
        R.kpi("materials", "Materials", "عدد الخامات", "Malzeme sayısı", "COUNT(*)",
              better="none"),
        R.kpi("never", "Never issued", "لم تُصرف مطلقًا", "Hiç çıkmamış",
              "SUM(CASE WHEN last_issue IS NULL THEN 1 ELSE 0 END)", better="down"),
        R.kpi("issued_value", "Issued value", "قيمة المصروف", "Çıkan değer",
              "SUM(issued_value)", better="none"),
        R.kpi("idle_value", "Value never issued", "قيمة الراكد", "Hiç çıkmamış değer",
              "SUM(CASE WHEN last_issue IS NULL THEN stock_value ELSE 0 END)",
              better="down"),
    ],
    filters=[
        R.filt("kind", "Kind", "النوع", "Tür", "m.kind", "select", "=", _KIND_OPTS),
        R.filt("code", "Code", "الكود", "Kod", "m.code"),
    ],
    chart=R.chart("pareto", "m.code",
                  "COALESCE(SUM(-v.qty*COALESCE(v.unit_cost,0)),0)", "m.code",
                  "Issued value by material", "قيمة المصروف حسب الخامة",
                  "Malzemeye göre çıkan değer"),
    icon="i-report",
)


# --- 4. finished goods on hand --------------------------------------------
_ONHAND = "(COALESCE(f.packed_qty,0)-COALESCE(f.shipped_qty,0))"

R.register(
    key="warehouse_fg", **_MOD,
    title="Finished goods packed and unshipped",
    title_ar="المنتج التام المعبأ وغير المشحون",
    title_tr="Paketlenmiş ve sevk edilmemiş mamul",
    desc="Packed minus shipped, per order, style, colour and size — the cartons "
         "physically standing in the finished-goods store right now.",
    desc_ar="المعبأ ناقص المشحون لكل أمر وموديل ولون ومقاس — الكراتين الموجودة فعليًا "
            "في مخزن المنتج التام الآن.",
    desc_tr="Sipariş, model, renk ve beden bazında paketlenen eksi sevk edilen — şu anda "
            "mamul deposunda fiilen duran koliler.",
    select=("o.order_no AS order_no, o.buyer AS buyer, f.style_code AS style, "
            "f.color AS color, f.size AS size, COALESCE(f.packed_qty,0) AS packed, "
            f"COALESCE(f.shipped_qty,0) AS shipped, {_ONHAND} AS on_hand, "
            "f.uom AS uom, f.warehouse AS warehouse, f.updated_at AS updated_at"),
    frm="wh_fg f LEFT JOIN ord_orders o ON o.id = f.order_id",
    date_col="f.updated_at",
    order=f"{_ONHAND} DESC, f.id ASC",
    columns=[
        R.col("order_no", "Order", "الأمر", "Sipariş"),
        R.col("buyer", "Buyer", "المشتري", "Alıcı"),
        R.col("style", "Style", "الموديل", "Model"),
        R.col("color", "Colour", "اللون", "Renk"),
        R.col("size", "Size", "المقاس", "Beden"),
        R.col("packed", "Packed", "المعبأ", "Paketlenen", "num",
              total="SUM(COALESCE(f.packed_qty,0))"),
        R.col("shipped", "Shipped", "المشحون", "Sevk edilen", "num",
              total="SUM(COALESCE(f.shipped_qty,0))"),
        R.col("on_hand", "In store", "بالمخزن", "Depoda", "num", total=f"SUM({_ONHAND})"),
        R.col("uom", "Unit", "الوحدة", "Birim"),
        R.col("warehouse", "Store", "المخزن", "Depo"),
        R.col("updated_at", "Last movement", "آخر حركة", "Son hareket", "date"),
    ],
    kpis=[
        R.kpi("skus", "SKUs", "عدد الأصناف", "SKU sayısı", "COUNT(*)", better="none"),
        R.kpi("packed", "Packed", "المعبأ", "Paketlenen", "SUM(COALESCE(f.packed_qty,0))"),
        R.kpi("shipped", "Shipped", "المشحون", "Sevk edilen",
              "SUM(COALESCE(f.shipped_qty,0))"),
        R.kpi("on_hand", "In store", "بالمخزن", "Depoda", f"SUM({_ONHAND})", better="none"),
    ],
    filters=[
        R.filt("order_no", "Order", "الأمر", "Sipariş", "o.order_no"),
        R.filt("buyer", "Buyer", "المشتري", "Alıcı", "o.buyer"),
        R.filt("style", "Style", "الموديل", "Model", "f.style_code"),
        R.filt("color", "Colour", "اللون", "Renk", "f.color"),
    ],
    chart=R.chart("bar", "f.style_code", f"SUM({_ONHAND})", "f.style_code",
                  "In store by style", "بالمخزن حسب الموديل", "Modele göre depoda"),
    icon="i-report",
)
