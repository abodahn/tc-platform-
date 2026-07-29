"""
Shipping reports — DECLARATIONS ONLY, executed by app/services/reporting.py.

Imported from the bottom of app/routes/shipping.py. Nothing here touches the
database at import time. Every report reads `shp_view`, the permission that opens
the module's own pages, and the engine enforces it on the HTML page and on the
CSV, XLSX and PDF exports alike.

Arithmetic is the module's own, kept identical to services.py so a report can
never disagree with the document on screen:
  * pieces  = qty_per_carton x cartons   (both stored per line)
  * CBM     = L x W x H in cm / 1 000 000 x cartons
  * shipped counts ONLY 'dispatched'/'delivered' — pieces that have physically left
  * a cancelled shipment never counts as packed or shipped
"""
from app.services import reporting as R

from .constants import MODES, SHIPMENT_STATUS, SHIPPED_STATUS

_MOD = dict(module="shipping", module_label="Shipments",
            module_label_ar="الشحنات", module_label_tr="Sevkiyatlar",
            perm="shp_view")

_PIECES = "COALESCE(SUM(c.qty_per_carton*c.cartons),0)"
_CBM = "COALESCE(SUM(c.length_cm*c.width_cm*c.height_cm/1000000.0*c.cartons),0)"
# Pieces on shipments that have actually left the factory. The status list is
# INLINE (a report column cannot bind a parameter), so it is asserted against the
# module's own constant: if SHIPPED_STATUS ever gains a status, this report would
# silently under-report shipped pieces while /shipping/recon counted them — two
# screens disagreeing about the same money. Fail at import instead.
assert set(SHIPPED_STATUS) == {"dispatched", "delivered"}, SHIPPED_STATUS
_SHIPPED = ("COALESCE(SUM(CASE WHEN s.status IN ('dispatched','delivered') "
            "THEN c.qty_per_carton*c.cartons ELSE 0 END),0)")

_STATUS_OPTS = [("planned", "Planned", "مخططة", "Planlandı"),
                ("packed", "Packed", "معبأة", "Paketlendi"),
                ("dispatched", "Dispatched", "مُرسَلة", "Sevk edildi"),
                ("delivered", "Delivered", "مُسلَّمة", "Teslim edildi"),
                ("cancelled", "Cancelled", "ملغاة", "İptal")]
assert {o[0] for o in _STATUS_OPTS} == set(SHIPMENT_STATUS)

_MODE_OPTS = [("sea", "Sea", "بحري", "Deniz"), ("air", "Air", "جوي", "Hava"),
              ("road", "Road", "بري", "Kara"), ("rail", "Rail", "سكة حديد", "Demiryolu"),
              ("courier", "Courier", "بريد سريع", "Kurye")]
assert {o[0] for o in _MODE_OPTS} == set(MODES)


# --- 1. shipment register --------------------------------------------------
R.register(
    key="shipping_register", **_MOD,
    title="Shipment register",
    title_ar="سجل الشحنات",
    title_tr="Sevkiyat kaydı",
    desc="Every shipment with its cartons, pieces and volume, filtered by ETD. "
         "This is the forwarder-facing view of what is booked and what has left.",
    desc_ar="كل شحنة مع كراتينها وقطعها وحجمها، مع تصفية حسب تاريخ المغادرة. هذه هي "
            "صورة ما هو محجوز وما غادر كما يراها وكيل الشحن.",
    desc_tr="Her sevkiyat; kolileri, adetleri ve hacmiyle, ETD'ye göre filtrelenir. "
            "Nakliyeciye dönük olarak neyin rezerve edildiğini ve neyin çıktığını gösterir.",
    select=("s.shipment_no AS shipment_no, s.buyer AS buyer, "
            "s.destination AS destination, s.mode AS mode, s.carrier AS carrier, "
            "s.container_no AS container_no, s.incoterm AS incoterm, s.status AS status, "
            "s.etd AS etd, s.eta AS eta, COALESCE(SUM(c.cartons),0) AS cartons, "
            f"{_PIECES} AS pieces, {_CBM} AS cbm"),
    frm="shp_shipments s LEFT JOIN shp_cartons c ON c.shipment_id = s.id",
    group=("s.id, s.shipment_no, s.buyer, s.destination, s.mode, s.carrier, "
           "s.container_no, s.incoterm, s.status, s.etd, s.eta"),
    date_col="s.etd",
    order="COALESCE(NULLIF(s.etd,''),'0000-00-00') DESC, s.id DESC",
    columns=[
        R.col("shipment_no", "Shipment", "الشحنة", "Sevkiyat"),
        R.col("buyer", "Buyer", "المشتري", "Alıcı"),
        R.col("destination", "Destination", "الوجهة", "Varış"),
        R.col("mode", "Mode", "وسيلة الشحن", "Taşıma şekli"),
        R.col("carrier", "Carrier", "الناقل", "Taşıyıcı"),
        R.col("container_no", "Container / AWB", "الحاوية / بوليصة الشحن", "Konteyner / AWB"),
        R.col("incoterm", "Incoterm", "شرط التسليم", "Teslim şekli"),
        R.col("status", "Status", "الحالة", "Durum"),
        R.col("etd", "ETD", "تاريخ المغادرة المتوقع", "Tahmini çıkış", "date"),
        R.col("eta", "ETA", "تاريخ الوصول المتوقع", "Tahmini varış", "date"),
        R.col("cartons", "Cartons", "الكراتين", "Koli", "num", total="SUM(cartons)"),
        R.col("pieces", "Pieces", "القطع", "Adet", "num", total="SUM(pieces)"),
        R.col("cbm", "CBM", "الحجم (م³)", "Hacim (m³)", "num", total="SUM(cbm)"),
    ],
    kpis=[
        R.kpi("shipments", "Shipments", "عدد الشحنات", "Sevkiyat sayısı", "COUNT(*)"),
        R.kpi("cartons", "Cartons", "الكراتين", "Koli", "SUM(cartons)"),
        R.kpi("pieces", "Pieces", "القطع", "Adet", "SUM(pieces)"),
        R.kpi("cbm", "Volume (CBM)", "الحجم (م³)", "Hacim (m³)", "SUM(cbm)", better="none"),
    ],
    filters=[
        R.filt("status", "Status", "الحالة", "Durum", "s.status", "select", "=",
               _STATUS_OPTS),
        R.filt("mode", "Mode", "وسيلة الشحن", "Taşıma şekli", "s.mode", "select", "=",
               _MODE_OPTS),
        R.filt("buyer", "Buyer", "المشتري", "Alıcı", "s.buyer"),
        R.filt("destination", "Destination", "الوجهة", "Varış", "s.destination"),
        R.filt("carrier", "Carrier", "الناقل", "Taşıyıcı", "s.carrier"),
    ],
    # NULLIF before COALESCE, exactly like the ORDER BY above: etd is blank ('') on
    # some rows and NULL on others, and substr('') is '' — without the NULLIF that
    # splits "no ETD" into TWO buckets, one of them drawn with an empty label, so the
    # bar marked '(no ETD)' under-reports the pieces that actually have no ETD.
    chart=R.chart("trend", "COALESCE(NULLIF(substr(s.etd,1,7),''),'(no ETD)')", _PIECES,
                  "COALESCE(NULLIF(substr(s.etd,1,7),''),'(no ETD)')",
                  "Pieces by ETD month", "القطع حسب شهر المغادرة",
                  "Çıkış ayına göre adet"),
    icon="i-report",
)


# --- 2. dispatch performance ----------------------------------------------
# Departure is read from the dispatched_at STAMP, exactly like update_shipment():
# a shipment cancelled after it left still departed, and the stamp is the only
# evidence a status change cannot erase.
_LEFT_DATE = "substr(s.dispatched_at,1,10)"
_HAS_ETD = "(s.etd IS NOT NULL AND s.etd <> '')"
_LATE = f"CASE WHEN {_HAS_ETD} AND {_LEFT_DATE} > s.etd THEN 1 ELSE 0 END"
_ONTIME = f"CASE WHEN {_HAS_ETD} AND {_LEFT_DATE} <= s.etd THEN 1 ELSE 0 END"
_PUNCT = (f"CASE WHEN NOT {_HAS_ETD} THEN 'No ETD' "
          f"WHEN {_LEFT_DATE} <= s.etd THEN 'On time' ELSE 'Late' END")

R.register(
    key="shipping_ontime", **_MOD,
    title="Dispatch performance (on-time departure)",
    title_ar="أداء المغادرة في الموعد",
    title_tr="Sevk performansı (zamanında çıkış)",
    desc="Shipments that have actually left, measured against their own ETD. A "
         "shipment with no ETD is counted as neither on time nor late, never as on time.",
    desc_ar="الشحنات التي غادرت فعليًا مقارنةً بتاريخ المغادرة المخطط لها. الشحنة بدون "
            "تاريخ مغادرة لا تُحتسب في الموعد ولا متأخرة.",
    desc_tr="Fiilen çıkmış sevkiyatlar, kendi ETD'lerine göre ölçülür. ETD'si olmayan bir "
            "sevkiyat ne zamanında ne de geç sayılır; asla zamanında sayılmaz.",
    select=("s.shipment_no AS shipment_no, s.buyer AS buyer, "
            "s.destination AS destination, s.mode AS mode, s.carrier AS carrier, "
            f"s.etd AS etd, {_LEFT_DATE} AS dispatched, s.status AS status, "
            f"{_PUNCT} AS punctuality"),
    frm="shp_shipments s",
    base_where=["s.dispatched_at IS NOT NULL AND s.dispatched_at <> ''"],
    date_col="s.dispatched_at",
    order="s.dispatched_at DESC, s.id DESC",
    columns=[
        R.col("shipment_no", "Shipment", "الشحنة", "Sevkiyat"),
        R.col("buyer", "Buyer", "المشتري", "Alıcı"),
        R.col("destination", "Destination", "الوجهة", "Varış"),
        R.col("mode", "Mode", "وسيلة الشحن", "Taşıma şekli"),
        R.col("carrier", "Carrier", "الناقل", "Taşıyıcı"),
        R.col("etd", "Planned ETD", "المغادرة المخططة", "Planlanan ETD", "date"),
        R.col("dispatched", "Actually left", "المغادرة الفعلية", "Fiili çıkış", "date"),
        R.col("punctuality", "Punctuality", "الالتزام بالموعد", "Zamanlama"),
        R.col("status", "Status", "الحالة", "Durum"),
    ],
    kpis=[
        R.kpi("dispatched", "Departed", "غادرت", "Çıktı", "COUNT(*)"),
        R.kpi("on_time", "On time", "في الموعد", "Zamanında", f"SUM({_ONTIME})"),
        R.kpi("late", "Late", "متأخرة", "Geç", f"SUM({_LATE})", better="down"),
        R.kpi("on_time_pct", "On-time %", "نسبة الالتزام %", "Zamanında %",
              f"ROUND(CAST(100.0*SUM({_ONTIME})/"
              f"NULLIF(SUM(CASE WHEN {_HAS_ETD} THEN 1 ELSE 0 END),0) AS NUMERIC),1)"),
    ],
    filters=[
        R.filt("punctuality", "Punctuality", "الالتزام بالموعد", "Zamanlama", _PUNCT,
               "select", "=", [("On time", "On time", "في الموعد", "Zamanında"),
                               ("Late", "Late", "متأخرة", "Geç"),
                               ("No ETD", "No ETD", "بدون تاريخ مغادرة", "ETD yok")]),
        R.filt("carrier", "Carrier", "الناقل", "Taşıyıcı", "s.carrier"),
        R.filt("mode", "Mode", "وسيلة الشحن", "Taşıma şekli", "s.mode", "select", "=",
               _MODE_OPTS),
        R.filt("buyer", "Buyer", "المشتري", "Alıcı", "s.buyer"),
    ],
    chart=R.chart("bar", "COALESCE(NULLIF(s.carrier,''),'(no carrier)')", f"SUM({_LATE})",
                  "COALESCE(NULLIF(s.carrier,''),'(no carrier)')",
                  "Late departures by carrier", "المغادرات المتأخرة حسب الناقل",
                  "Taşıyıcıya göre geç çıkışlar"),
    icon="i-report",
)


# --- 3. ordered vs packed vs shipped --------------------------------------
# The cancelled-shipment test lives in the JOIN's ON clause, not in base_where:
# in the WHERE clause it would turn the LEFT JOIN into an inner one and drop
# every order that has no shipment yet — which is precisely the order most at
# risk of shipping short.
R.register(
    key="shipping_recon", **_MOD,
    title="Ordered vs packed vs shipped",
    title_ar="المطلوب مقابل المعبأ مقابل المشحون",
    title_tr="Sipariş, paketlenen ve sevk edilen",
    desc="Order quantity against what is packed and what has physically left. Short "
         "and over shipments are both buyer chargebacks; sort by fulfilment to find them.",
    desc_ar="كمية الأمر مقابل المعبأ وما غادر فعليًا. النقص والزيادة كلاهما يعرّض المصنع "
            "لخصم من المشتري؛ رتّب حسب نسبة التنفيذ للعثور عليهما.",
    desc_tr="Sipariş miktarı ile paketlenen ve fiilen çıkan miktar. Eksik ve fazla "
            "sevkiyatın ikisi de alıcı cezasıdır; bulmak için gerçekleşmeye göre sıralayın.",
    select=("o.order_no AS order_no, o.buyer AS buyer, o.style_ref AS style_ref, "
            "o.status AS order_status, o.ship_date AS ship_date, "
            f"COALESCE(o.qty,0) AS ordered, {_PIECES} AS packed, {_SHIPPED} AS shipped, "
            f"(COALESCE(o.qty,0) - {_SHIPPED}) AS balance, "
            f"ROUND(CAST(100.0*{_SHIPPED}/NULLIF(o.qty,0) AS NUMERIC),1) AS fulfil_pct, "
            "COUNT(DISTINCT s.id) AS shipments"),
    frm=("ord_orders o "
         "LEFT JOIN shp_shipments s ON s.order_id = o.id "
         "AND COALESCE(s.status,'') <> 'cancelled' "
         "LEFT JOIN shp_cartons c ON c.shipment_id = s.id"),
    group="o.id, o.order_no, o.buyer, o.style_ref, o.status, o.ship_date, o.qty",
    date_col="o.ship_date",
    order="COALESCE(NULLIF(o.ship_date,''),'9999-12-31') ASC, o.id DESC",
    columns=[
        R.col("order_no", "Order", "الأمر", "Sipariş"),
        R.col("buyer", "Buyer", "المشتري", "Alıcı"),
        R.col("style_ref", "Style ref", "مرجع الموديل", "Model referansı"),
        R.col("order_status", "Order status", "حالة الأمر", "Sipariş durumu"),
        R.col("ship_date", "Ex-factory", "تاريخ الخروج من المصنع", "Fabrika çıkışı", "date"),
        R.col("ordered", "Ordered", "المطلوب", "Sipariş edilen", "num",
              total="SUM(ordered)"),
        R.col("packed", "Packed", "المعبأ", "Paketlenen", "num", total="SUM(packed)"),
        R.col("shipped", "Shipped", "المشحون", "Sevk edilen", "num", total="SUM(shipped)"),
        R.col("balance", "Balance", "المتبقي", "Kalan", "num", total="SUM(balance)"),
        R.col("fulfil_pct", "Fulfilment %", "نسبة التنفيذ %", "Gerçekleşme %", "num"),
        R.col("shipments", "Shipments", "عدد الشحنات", "Sevkiyat sayısı", "num",
              total="SUM(shipments)"),
    ],
    kpis=[
        R.kpi("orders", "Orders", "عدد الأوامر", "Sipariş sayısı", "COUNT(*)",
              better="none"),
        R.kpi("ordered", "Ordered", "المطلوب", "Sipariş edilen", "SUM(ordered)",
              better="none"),
        R.kpi("shipped", "Shipped", "المشحون", "Sevk edilen", "SUM(shipped)"),
        R.kpi("balance", "Still to ship", "المتبقي للشحن", "Sevk edilecek",
              "SUM(balance)", better="down"),
        R.kpi("fulfil", "Fulfilment %", "نسبة التنفيذ %", "Gerçekleşme %",
              "ROUND(CAST(100.0*SUM(shipped)/NULLIF(SUM(ordered),0) AS NUMERIC),1)"),
    ],
    filters=[
        R.filt("order_no", "Order", "الأمر", "Sipariş", "o.order_no"),
        R.filt("buyer", "Buyer", "المشتري", "Alıcı", "o.buyer"),
        R.filt("order_status", "Order status", "حالة الأمر", "Sipariş durumu", "o.status"),
    ],
    chart=R.chart("bar", "COALESCE(NULLIF(o.buyer,''),'(no buyer)')", _SHIPPED,
                  "COALESCE(NULLIF(o.buyer,''),'(no buyer)')",
                  "Shipped pieces by buyer", "القطع المشحونة حسب المشتري",
                  "Alıcıya göre sevk edilen adet"),
    icon="i-report",
)
