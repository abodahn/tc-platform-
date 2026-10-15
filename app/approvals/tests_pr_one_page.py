"""The Purchase Request prints on ONE page — always.

Asked for 2026-10-15: "it can't be two pages, only one page". A 4-line request
used to spill its last four signature boxes onto page 2, so the signatures got
separated from the request they authorise.

pr_pdf now measures the body first and, only when it is too tall, shrinks it
uniformly onto the page. This file walks the request UP — far past any real one
— and proves that:

  * every size prints on exactly one page
  * an ordinary request is NOT shrunk (full-size type)
  * nothing is dropped to make it fit: every line's vendor, every signer and
    every verification code is still on the page, whole
  * the signature boxes are named by the step they sign, never a raw role key

    python app/approvals/tests_pr_one_page.py
"""
import io
import os
import re
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="pr1page_"), "p.db")
    from app import create_app
    return create_app()


STAGES = ["warehouse", "warehouse_manager", "purchasing", "factory_manager", "scd",
          "cfo", "ceo", "bod"]


def bundle(n_lines, n_steps, long_text=False, escalate=False):
    """A request shaped as get_pr() returns it, sized on demand."""
    desc = ("Heavy-duty industrial sewing machine spare part, stainless, "
            "per OEM drawing rev C, packed individually, with certificate " * 2
            if long_text else "spare part")
    items = [{"item": ("SP-%04d كروشيه صغير اوفر" % i) if long_text else "SP-%04d" % i,
              "description": desc, "unit": "Pcs", "qty": 3 + i, "current_stock": i,
              "stock_days": 12, "pending_qty": 2, "last_order_qty": 5,
              "last_order_date": "2026-09-14", "vendor": "Vendor Line %03d" % i,
              "unit_price": 1000 + i, "est_cost": (1000 + i) * (3 + i),
              "last_order_price": 990, "last_stock": 4, "eta": "2026-11-30",
              "lead_time_days": 14}
             for i in range(1, n_lines + 1)]
    steps = []
    for k in range(n_steps):
        st = STAGES[k % len(STAGES)]
        steps.append({"stage": st, "seq": k + 1, "status": "approved" if k % 3 else "pending",
                      "approver_role": "warehouse_manager", "approver_name": "Signer %02d" % k,
                      "acted_at": "2026-10-1%d 10:00:00" % (k % 10),
                      "verify_code": "CODE%08d" % k, "action": "approve" if k % 4 == 3 else "review",
                      "esc_from": "purchasing_manager" if (escalate and k == 1) else None,
                      "esc_role": "cfo" if (escalate and k == 1) else None})
    pr = {"pr_no": "PR-2026-999999", "status": "pending", "currency": "EGP",
          "request_date": "2026-10-15", "requester": "req.tester",
          "requester_name": "Requester Tester", "request_for": "SN-3L3DA00578 · JUKI",
          "department": "Production",
          "title": ("كروشيهات اوفر مخزون استوك — " * 6) if long_text else "Spares",
          "total": sum(it["est_cost"] for it in items), "tax_rate": 0,
          "payment_condition": "Net 30", "delivery_condition": "T&C Warehouse",
          "so_no": "SO-1", "forecast_ref": "FC-1", "cost_center": "CC-1",
          "asset_code": "AS-1", "expenditure_kind": "opex"}
    return {"pr": pr, "items": items, "steps": steps, "quotes": []}


def run():
    app = _app()
    with app.app_context():
        from app.approvals import pdf as P
        try:
            from pypdf import PdfReader
        except ImportError:
            PdfReader = None

        def read(raw):
            if PdfReader is None:
                return None, None
            r = PdfReader(io.BytesIO(raw))
            return len(r.pages), "\n".join((p.extract_text() or "") for p in r.pages)

        def shrink(raw):
            """The uniform shrink pr_pdf applied (1.0 = full size). reportlab
            folds the centring translate into it: "s 0 0 s tx 0 cm" — equal
            factors and a zero y offset, which an image placement
            (w 0 0 h x y cm) does not have."""
            m = re.search(rb"(?<![0-9.])(0?\.[0-9]+) 0 0 \1 [0-9.]+ 0 cm", _content(raw))
            return float(m.group(1)) if m else 1.0

        print("\n1. every size prints on exactly one page")
        cases = [("1 line, 1 signer", bundle(1, 1)),
                 ("4 lines, 6 signers (the PR that used to spill)", bundle(4, 6)),
                 ("12 lines, 8 signers", bundle(12, 8)),
                 ("40 lines, long Arabic text, escalation", bundle(40, 8, True, True)),
                 ("120 lines, 14 signers (two signature rows)", bundle(120, 14, True, True))]
        for label, b in cases:
            raw = P.pr_pdf(b)
            pages, text = read(raw)
            if pages is None:
                chk("pypdf available to count pages", False, "not installed")
                break
            chk("%-48s -> %d page(s)" % (label, pages), pages == 1, pages)

            # ---- nothing dropped to make it fit ---------------------------
            flat = re.sub(r"\s+", "", text)
            missing = [it["vendor"] for it in b["items"]
                       if it["vendor"].replace(" ", "") not in flat]
            chk("   every line's vendor is on the page (%d lines)" % len(b["items"]),
                not missing, missing[:3])
            names = [s["approver_name"] for s in b["steps"]]
            gone = [n for n in names if n.replace(" ", "") not in flat]
            chk("   every signer is named", not gone, gone[:3])
            codes = [s["verify_code"] for s in b["steps"] if s["status"] == "approved"]
            cut = [c for c in codes if c not in flat]
            chk("   every verification code is printed WHOLE", not cut, cut[:3])
            chk("   no raw role key in a signature box", "warehouse_manager" not in text)

        print("\n2. an ordinary request is printed at full size, not shrunk")
        sc = shrink(P.pr_pdf(bundle(4, 6)))
        chk("4 lines / 6 signers: full size (scale %.3f)" % sc, sc == 1.0)
        sc = shrink(P.pr_pdf(bundle(8, 8)))
        chk("8 lines / 8 signers: full size (scale %.3f)" % sc, sc == 1.0)
        sc = shrink(P.pr_pdf(bundle(10, 8)))
        chk("10 lines / 8 signers: at most a light shrink (scale %.3f)" % sc, sc >= 0.9)
        sc = shrink(P.pr_pdf(bundle(120, 14, True, True)))
        chk("120 lines / 14 signers: shrunk to fit (scale %.3f)" % sc, 0 < sc < 1.0)

    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


def _content(raw):
    """Decoded content streams (reportlab writes ASCII85 + Flate)."""
    import base64
    import zlib
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\s*endstream", raw, re.S):
        data = m.group(1)
        for step in (lambda x: base64.a85decode(x, adobe=True), zlib.decompress):
            try:
                data = step(data)
            except Exception:  # noqa: BLE001 — image / raw stream
                pass
        out.append(data)
    return b"\n".join(out)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
