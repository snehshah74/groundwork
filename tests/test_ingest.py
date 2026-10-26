"""Documents (PDF, Word, Markdown) become evidence, and can seed the product canvas."""
import io
import zipfile

import pytest

from app import ai, ingest
from conftest import signup
from docs_fixtures import make_docx, make_pdf

LONG = ("Finance admins told us they rebuild the month-end report by hand from six separate exports every single month. "
        "It takes half a day, and any mistake means starting over. They would pay for one consolidated report. ") * 3


def test_docx_text_headings_and_tables_are_extracted():
    raw = make_docx([("Month-end interviews", "Heading1"), "Admins rebuild the report by hand from six exports each month.", "Tom & Jerry <script>"],
                    tables=[[["Customer", "Pain"], ["Acme", "Six CSVs to merge"]]])
    text = ingest.extract_text("notes.docx", raw)
    assert "Month-end interviews" in text and "six exports" in text and "Tom & Jerry <script>" in text and "Six CSVs to merge" in text


def test_pdf_text_is_extracted_across_pages():
    text = ingest.extract_text("report.pdf", make_pdf([["Interview one: export is slow", "It takes hours"], ["Interview two: sync created duplicates"]]))
    assert "export is slow" in text and "sync created duplicates" in text


def test_markdown_and_text_pass_through():
    assert ingest.extract_text("a.md", b"# Title\n\nSome \xc3\xa9vidence") == "# Title\n\nSome \xe9vidence"


def test_hostile_and_broken_files_are_refused_with_plain_errors():
    with pytest.raises(ValueError, match="Word document"):
        ingest.extract_text("x.docx", b"this is not a zip")
    with pytest.raises(ValueError, match="PDF"):
        ingest.extract_text("x.pdf", b"%PDF-1.4 truncated garbage")
    with pytest.raises(ValueError, match="not supported"):
        ingest.extract_text("virus.exe", b"MZ")
    # entity expansion bomb inside a docx
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>&c;</w:t></w:r></w:p></w:body></w:document>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", bomb)
    with pytest.raises(ValueError, match="Word document"):
        ingest.extract_text("bomb.docx", out.getvalue())
    # zip bomb: tiny file that inflates to hundreds of megabytes
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w:document>" + "a" * (60 * 1024 * 1024) + "</w:document>")
    assert len(out.getvalue()) < 200_000
    with pytest.raises(ValueError, match="too large"):
        ingest.extract_text("zipbomb.docx", out.getvalue())


def test_encrypted_and_image_only_pdfs_get_helpful_messages():
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for page in PdfReader(io.BytesIO(make_pdf([["secret"]]))).pages:
        w.add_page(page)
    w.encrypt("hunter2")
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(ValueError, match="password"):
        ingest.extract_text("locked.pdf", buf.getvalue())
    with pytest.raises(ValueError, match="scanned"):
        ingest.extract_text("scan.pdf", make_pdf([[]]))


def test_page_limit_is_enforced():
    pdf = make_pdf([[f"page {n}"] for n in range(ingest.MAX_PDF_PAGES + 5)])
    text = ingest.extract_text("long.pdf", pdf)
    assert f"page {ingest.MAX_PDF_PAGES - 1}" in text and f"page {ingest.MAX_PDF_PAGES + 2}" not in text


def test_long_documents_are_merged_into_passages_but_quote_lists_stay_one_per_item():
    short = ["Export is slow.", "Sync duplicated invoices.", "Too many emails."]
    assert ingest.chunk_text("\n\n".join(short), merge=False) == short                    # a list of quotes: one each
    merged = ingest.chunk_text("\n\n".join(short + ["Another short note from a customer interview."]), merge=True)
    assert len(merged) == 1 and "Export is slow." in merged[0]                             # a document: passages
    big = ingest.chunk_text(LONG * 4, merge=True)
    assert len(big) > 1 and all(len(c) <= ingest.PASSAGE_CHARS + 120 for c in big)
    assert ingest.chunk_text("12\n\nPage 3 of 9\n\nOK", merge=False) == []                 # page numbers and fragments are dropped
    assert ingest.refs("a.pdf", ["x"]) == ingest.refs("a.pdf", ["x"]) != ingest.refs("b.pdf", ["x"])


def upload(c, name, data, ctype="application/octet-stream"):
    return c.post("/api/evidence/upload", files={"file": (name, data, ctype)})


def test_upload_pdf_docx_and_markdown_and_reupload_never_duplicates(client):
    owner = signup(client, "o@x.com")
    docx = make_docx(["Interview with Acme finance team about month-end close and the pain of merging exports by hand."], )
    r = upload(owner, "acme-interview.docx", docx)
    assert r.status_code == 200 and r.json()["added"] == 1
    again = upload(owner, "acme-interview.docx", docx).json()
    assert again["added"] == 0 and again["skipped"] == 1
    r = upload(owner, "usage.pdf", make_pdf([["Support tickets about export, a pattern across many accounts every month end."]]))
    assert r.json()["added"] == 1
    r = upload(owner, "quotes.md", b"# Quotes\n\nThe export takes hours every single month for us.\n\nSync created forty duplicate invoices overnight.")
    assert r.json()["added"] >= 2
    ev = owner.get("/api/evidence").json()
    assert {e["source"] for e in ev} >= {"doc: acme-interview.docx", "doc: usage.pdf"}


def test_upload_errors_are_clear(client):
    owner = signup(client, "o@x.com")
    assert upload(owner, "x.exe", b"MZ").status_code == 400
    assert "Word document" in upload(owner, "x.docx", b"nope").json()["detail"]
    assert upload(owner, "empty.txt", b"   \n\n  ").json()["added"] == 0
    big = b"a" * (ingest.MAX_UPLOAD_BYTES + 1)
    assert upload(owner, "big.txt", big).status_code == 413


def test_a_big_document_is_truncated_not_rejected(client, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_PASSAGES", 5)
    owner = signup(client, "o@x.com")
    r = upload(owner, "book.md", ("\n\n".join(f"Customer number {n} described a distinct problem with exporting their reports at month end." for n in range(30))).encode()).json()
    assert r["added"] == 5 and r["truncated"] is True


def test_canvas_from_a_document_sets_overview_and_fills_the_canvas(client):
    owner = signup(client, "o@x.com")
    doc = make_docx(["Ledgerly is invoicing software for freelancers and small agencies. It sends invoices, tracks payments and syncs with QuickBooks.",
                     "Our customers are owner-operators who do their own bookkeeping."])
    r = owner.post("/api/product/from-doc", files={"file": ("brief.docx", doc, "application/octet-stream")})
    assert r.status_code == 200, r.text
    p = owner.get("/api/product").json()
    assert "invoicing software" in p["overview"] and r.json()["mode"] == "offline"
    assert p["updated_at"] and p["canvas"]["customer_segments"]


def test_canvas_from_a_document_with_claude_keeps_an_existing_name(client, monkeypatch):
    owner = signup(client, "o@x.com")
    owner.put("/api/product", json={"name": "My Product"})

    def fake(system, user, max_tokens=4000):
        if "name" in system and "overview" in system and "canvas" not in system.lower():
            return {"name": "Ledgerly", "overview": "Invoicing for freelancers."}
        return {k: f"{k} text" for k in ai.CANVAS_KEYS}
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_ask", fake)
    r = owner.post("/api/product/from-doc", files={"file": ("brief.md", b"Ledgerly is invoicing for freelancers.", "text/markdown")})
    assert r.json()["mode"] == "claude"
    p = owner.get("/api/product").json()
    assert p["name"] == "My Product" and p["overview"] == "Invoicing for freelancers." and p["canvas"]["problem"] == "problem text"
