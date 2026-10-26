"""Build small real PDF and Word files for tests, without any extra library."""
import io
import zipfile


def make_docx(paragraphs, tables=()):
    def p(t, style=None):
        s = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        t = t.replace("&", "&amp;").replace("<", "&lt;")
        return f'<w:p>{s}<w:r><w:t xml:space="preserve">{t}</w:t></w:r></w:p>'
    body = "".join(p(*t) if isinstance(t, tuple) else p(t) for t in paragraphs)
    for rows in tables:
        body += "<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc>{p(c)}</w:tc>" for c in r) + "</w:tr>" for r in rows) + "</w:tbl>"
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", xml)
    return out.getvalue()


def make_pdf(pages):
    """pages: list of lists of text lines. One Helvetica text object per page."""
    objs, kids = [], []
    n_pages = len(pages)
    page_ids = [3 + 2 * i for i in range(n_pages)]
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] /Count {n_pages} >>")
    font_id = 3 + 2 * n_pages
    for i, lines in enumerate(pages):
        stream = "BT /F1 12 Tf 72 720 Td 16 TL " + " ".join(f"({l.replace('(', '[').replace(')', ']')}) Tj T*" for l in lines) + " ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
