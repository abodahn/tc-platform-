"""Purchase requests are written in English only.

The request is read by buyers, suppliers (RFQ / PO PDFs) and auditors, so every
free-text field the requester types must be English. Catalogue items keep their
master name (the requester did not type it), and the pickers (department,
machine/line, unit) are not free text.
"""
from app.approvals.arabic_pdf import has_arabic

# Header fields a requester types, with the label used in the refusal.
_HEADER = (("title", "Title"), ("request_for_free", "Request for"), ("vendor", "Suggested vendor"),
           ("notes", "Notes"), ("so_no", "Sales order"), ("forecast_ref", "Forecast ref"),
           ("cost_center", "Cost centre"), ("asset_code", "Asset code"))
# Line fields; item[] only counts when the line is not a catalogue / spare pick.
_LINE = (("description[]", "description"), ("item_notes[]", "notes"), ("vendor[]", "vendor"))


def arabic_in_form(form):
    """Labels of the fields holding Arabic text, e.g. ["Title", "Line 2 description"]."""
    bad = [label for key, label in _HEADER if has_arabic(form.get(key, ""))]
    items = form.getlist("item[]")
    picked = [bool((a or "").strip() or (b or "").strip()) for a, b in
              zip(form.getlist("item_id[]") + [""] * len(items),
                  form.getlist("spare_id[]") + [""] * len(items))]
    for i, name in enumerate(items):
        if not (picked[i] if i < len(picked) else False) and has_arabic(name):
            bad.append("Line %d item" % (i + 1))
    for key, label in _LINE:
        for i, v in enumerate(form.getlist(key)):
            if has_arabic(v):
                bad.append("Line %d %s" % (i + 1, label))
    return list(dict.fromkeys(bad))


REFUSAL = "Write the request in English only. Arabic text is not accepted in: "
