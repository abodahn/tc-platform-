# -*- coding: utf-8 -*-
"""ONE roster parser for the two front doors that create sign-in accounts.

Admin -> Users -> Import and scripts/seed_users_from_excel.py each carried their
own copy of this, and the copies drifted apart inside one afternoon: both ended
up flattening every worksheet into a single grid, so a "Departments" tab was
read through the Staff tab's column map and seven department names became real
logins. They call this module now, so they cannot drift again.

Rules that are not negotiable here, because the output of a mistake is an
account somebody can sign in with:

* Sheets are parsed SEPARATELY (app.tabular.read_sheets). A sheet with no
  recognisable header contributes nobody — it is not fed through the previous
  sheet's columns.
* A header row must show MORE THAN ONE known column and must sit within the
  first `HDR_SCAN` rows OF ITS OWN SHEET. A single hit is not a header: an
  Exchange address list contains real shared mailboxes literally named 'mail',
  'smtp', 'alias' and 'login', and binding on one of those both dropped that
  person and read every row after them from the wrong columns.
* Once a sheet's header is bound it is never rebound from that sheet's data.
"""
from app.tabular import read_sheets, TableError      # noqa: F401 — re-exported

# Header aliases (lower-cased, whitespace-collapsed) -> field.
HDR = {
    "username": ("alias", "username", "user name", "login", "samaccountname", "user id"),
    "full_name": ("display name", "name", "full name", "displayname"),
    "email": ("primary smtp address", "email", "e-mail", "mail", "smtp", "email address"),
}
HDR_SCAN = 6            # a header is at the top of its sheet or it is not a header


def _norm(v):
    return "" if v is None else " ".join(str(v).split())


def _hdr_map(row):
    """{field: column index} for the known column names this row carries."""
    cells = [_norm(c).lower() for c in row]
    found = {}
    for field, names in HDR.items():
        for j, c in enumerate(cells):
            if c in names:
                found[field] = j
                break
    return found


def _find_header(rows):
    """(columns, row index) for the widest header in this sheet's scan window.

    Widest rather than first, so a title row that happens to say "Name" does not
    win over the real header two rows below it. `None` when nothing in the window
    shows at least two known columns.
    """
    best, at = {}, -1
    for i, row in enumerate(rows[:HDR_SCAN]):
        found = _hdr_map(row)
        if len(found) > len(best):
            best, at = found, i
    return (best, at) if len(best) > 1 else (None, -1)


def safe_fragment(text, limit=200):
    """Text from an uploaded file, safe to hand to a flash message.

    The flash lands inside an inline <script> in base.html, where HTML escaping
    does not apply: a column named `</script>` or ending in a backslash would end
    the string, or the tag. Quotes, backslashes and angle brackets go away here.
    """
    out = _norm(text).replace('"', "'").replace("\\", "/")
    return out.replace("<", "(").replace(">", ")")[:limit]


def columns_seen(sheets, limit=12):
    """The widest row in any sheet's scan window, cleaned for display — so a
    failed import can show WHAT it read instead of only that it failed."""
    best = []
    for _name, rows in sheets:
        for row in rows[:HDR_SCAN]:
            seen = [safe_fragment(c, 40) for c in row if _norm(c)]
            if len(seen) > len(best):
                best = seen
    return ", ".join(best[:limit])


def parse_roster(src):
    """(users, reason, columns) from a path / bytes / upload of any table format.

    users    [{"username", "full_name", "email"}] from every sheet that had a
             header; duplicates are left in — the callers dedupe on insert.
    reason   None when users is non-empty, else "no_header" (no sheet carried a
             usable header row) or "no_rows" (a header, but nothing under it).
    columns  display string of the widest scanned row, for the failure message.

    Raises TableError when the file itself cannot be read (a PDF, an .ods, an
    empty upload); the caller decides how to say so.
    """
    sheets, _meta = read_sheets(src)
    users, any_header = [], False
    for _name, rows in sheets:
        cols, at = _find_header(rows)
        if cols is None:
            continue                       # a cover page, a "Departments" tab, junk
        any_header = True
        for row in rows[at + 1:]:
            if len(_hdr_map(row)) > 1:
                continue                   # a repeated header (Excel print titles)

            def cell(field, _row=row):
                j = cols.get(field)
                return _norm(_row[j]) if (j is not None and j < len(_row)) else ""

            email = cell("email")
            username = (cell("username") or email.split("@")[0]).lower()
            if username:
                users.append({"username": username, "full_name": cell("full_name"),
                              "email": email})
    reason = None if users else ("no_rows" if any_header else "no_header")
    return users, reason, columns_seen(sheets)


if __name__ == "__main__":          # self-check: python -m app.user_roster
    import io as _io
    import openpyxl

    def eq(label, got, want):
        assert got == want, f"{label}: got {got!r}, want {want!r}"
        print("  ok  ", label)

    def book(*sheets):
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name, rows in sheets:
            ws = wb.create_sheet(name)
            for r in rows:
                ws.append(r)
        buf = _io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    HEAD = ["Display Name", "Alias", "Primary SMTP Address"]

    # 1. A second worksheet must NOT be read through the first sheet's columns.
    #    This shipped: "Departments" became seven sign-in accounts.
    users, reason, _ = parse_roster(book(
        ("Staff", [HEAD, ["Ahmed ElGohary", "aelgohary", "a@tc.com"],
                   ["Mona Said", "msaid", "m@tc.com"]]),
        ("Departments", [["Department", "Head"], ["Sewing", "Mona Said"],
                         ["Cutting", "Ali Nour"], ["Mail", "Sara Adel"]])))
    eq("only the sheet with a header creates users",
       [u["username"] for u in users], ["aelgohary", "msaid"])
    eq("...and no reason to report", reason, None)

    # 2. A cover page pushes the real header past row 6 of the WORKBOOK, but it
    #    is still row 1 of its own sheet. The flat 6-row window imported nobody.
    users, reason, _ = parse_roster(book(
        ("Cover", [["T&C Garments"], ["Confidential"], [""], ["Prepared by IT"],
                   [""], ["Rev 3"], [""], ["Fill in the Staff sheet"]]),
        ("Staff", [HEAD, ["Ahmed ElGohary", "aelgohary", "a@tc.com"]])))
    eq("a cover sheet does not hide the roster",
       [u["username"] for u in users], ["aelgohary"])

    # 3. A mailbox literally called 'mail' is a person, not a header: it must be
    #    imported, and it must not rebind the columns for everyone after it.
    users, _, _ = parse_roster(book(("Staff", [
        HEAD, ["Reception", "mail", "mail@tc.com"],
        ["Ahmed ElGohary", "aelgohary", "a@tc.com"]])))
    eq("a 'mail' alias is a user, not a header",
       [(u["username"], u["full_name"]) for u in users],
       [("mail", "Reception"), ("aelgohary", "Ahmed ElGohary")])

    # 4. Junk above the header, within the window.
    users, _, _ = parse_roster(book(("Staff", [
        ["ALL TC — exported 2026-08-01"], [""], HEAD,
        ["Ahmed ElGohary", "aelgohary", "a@tc.com"]])))
    eq("junk rows above the header are skipped",
       [u["username"] for u in users], ["aelgohary"])

    # 5. Nothing recognisable anywhere -> a reason and the columns we did read.
    users, reason, cols = parse_roster(book(
        ("Departments", [["Department", "Head"], ["Sewing", "Mona Said"]])))
    eq("no header -> no users", users, [])
    eq("no header -> reason", reason, "no_header")
    eq("no header -> columns reported", cols, "Department, Head")

    # 6. A header with nothing under it is a different failure from no header.
    users, reason, _ = parse_roster(book(("Staff", [HEAD])))
    eq("header but no rows", (users, reason), ([], "no_rows"))

    # 7. CSV still works, and the username falls back to the email local part.
    users, reason, _ = parse_roster("Display Name;Primary SMTP Address\n"
                                    "Ahmed ElGohary;A.ElGohary@tc.com\n".encode("utf-8"))
    eq("semicolon csv + email fallback", users,
       [{"username": "a.elgohary", "full_name": "Ahmed ElGohary",
         "email": "A.ElGohary@tc.com"}])

    # 8. One known column is NOT a header — that is how a department named 'Mail'
    #    used to mint accounts. Loud failure beats silent accounts.
    users, reason, _ = parse_roster(book(
        ("Sheet1", [["Mail"], ["Sewing"], ["Cutting"]])))
    eq("a single alias hit does not bind", (users, reason), ([], "no_header"))

    # 9. The reported column list is quoted into an inline <script>; a column name
    #    must not be able to close it.
    _, _, cols = parse_roster(book(
        ("Sheet1", [['</script>alert(1)', 'ends with a backslash \\']])))
    eq("column names cannot break out of the script tag", cols,
       "(/script)alert(1), ends with a backslash /")

    try:
        parse_roster(b"")
        raise AssertionError("an empty file must raise")
    except TableError:
        print("  ok   empty file refused with a reason")

    print("app/user_roster.py self-check PASSED")
