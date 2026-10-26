"""Turn uploaded documents (PDF, Word, Markdown, text) into evidence passages.

Uploads are untrusted files, so every step is bounded: file size, PDF pages, decompressed Word size, extracted text,
and number of passages. Word XML is parsed with defusedxml, which refuses entity-expansion bombs.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_TEXT_CHARS = 600_000
MAX_PASSAGES = 300          # per document; the rest is reported as truncated, not silently dropped
PASSAGE_CHARS = 900         # long documents are cut into passages about this size
MIN_CHARS = 12              # shorter fragments (page numbers, "OK") are not evidence
_DOCX_MAX_XML = 20 * 1024 * 1024
_DOCX_MAX_TOTAL = 100 * 1024 * 1024
_TEXT_TYPES = {"txt", "md", "markdown"}


def _ext(filename: str) -> str:
    return filename.lower().rsplit(".", 1)[-1] if "." in filename else ""


def extract_text(filename: str, raw: bytes) -> str:
    """Plain text of a document. Raises ValueError with a message that can be shown to the person who uploaded it."""
    ext = _ext(filename)
    if ext in _TEXT_TYPES:
        text = raw.decode("utf-8", errors="replace")
    elif ext == "pdf":
        text = _pdf_text(raw)
    elif ext == "docx":
        text = _docx_text(raw)
    else:
        raise ValueError("That file type is not supported. Use PDF, Word (.docx), Markdown, text or CSV.")
    return text[:MAX_TEXT_CHARS]


def _pdf_text(raw: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("This PDF is password protected. Remove the password and upload it again.")
        parts = [(page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES]]
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("That does not look like a readable PDF.") from e
    text = "\n\n".join(parts)
    if not text.strip():
        raise ValueError("No readable text was found. This PDF may be a scanned image: run OCR on it first, then upload it again.")
    return text


def _docx_text(raw: bytes) -> str:
    from defusedxml import ElementTree as ET
    from defusedxml.common import DefusedXmlException

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if sum(i.file_size for i in z.infolist()) > _DOCX_MAX_TOTAL:
                raise ValueError("This document is too large to read safely.")
            with z.open("word/document.xml") as f:
                xml = f.read(_DOCX_MAX_XML + 1)      # bounded read: a zip header can lie about the size
            if len(xml) > _DOCX_MAX_XML:
                raise ValueError("This document is too large to read safely.")
        root = ET.fromstring(xml)
    except ValueError as e:
        if isinstance(e, DefusedXmlException) or "too large" not in str(e):
            raise ValueError("That does not look like a Word document (.docx).") from e
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise ValueError("That does not look like a Word document (.docx).") from e
    paras = []
    for p in root.iter(w + "p"):
        parts = []
        for node in p.iter():
            if node.tag == w + "t":
                parts.append(node.text or "")
            elif node.tag == w + "tab":
                parts.append("\t")
            elif node.tag in (w + "br", w + "cr"):
                parts.append("\n")
        paras.append("".join(parts).strip())
    return "\n\n".join(paras)


def _split_long(paragraph: str) -> list[str]:
    """Cut an over-long paragraph at sentence ends, and at the limit when a single sentence is longer than that."""
    if len(paragraph) <= PASSAGE_CHARS:
        return [paragraph]
    out, cur = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
        while len(sentence) > PASSAGE_CHARS:
            if cur:
                out.append(cur)
                cur = ""
            out.append(sentence[:PASSAGE_CHARS])
            sentence = sentence[PASSAGE_CHARS:]
        if cur and len(cur) + len(sentence) + 1 > PASSAGE_CHARS:
            out.append(cur)
            cur = ""
        cur = f"{cur} {sentence}".strip()
    if cur:
        out.append(cur)
    return out


def chunk_text(text: str, merge: bool) -> list[str]:
    """Evidence items from text. merge=False keeps one item per paragraph (a list of quotes). merge=True (long documents)
    joins hard-wrapped lines and packs short paragraphs together into passages of about PASSAGE_CHARS."""
    paras = [re.sub(r"[ \t]+", " ", p).strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n"))]
    paras = [p for p in paras if p]
    if not merge:
        return [p for p in paras if len(p) >= MIN_CHARS]
    out: list[str] = []
    cur = ""
    for p in paras:
        for piece in _split_long(" ".join(p.split("\n"))):
            if cur and len(cur) + len(piece) + 2 > PASSAGE_CHARS:
                out.append(cur)
                cur = ""
            cur = f"{cur} {piece}".strip()
    if cur:
        out.append(cur)
    return [c for c in out if len(c) >= MIN_CHARS]


def refs(filename: str, chunks: list[str]) -> list[str]:
    """Stable references, so uploading the same document twice never duplicates its passages."""
    return ["doc:" + hashlib.sha1(f"{filename}\0{c}".encode()).hexdigest()[:16] for c in chunks]


def to_evidence(filename: str, text: str, merge: bool) -> tuple[list[dict], bool]:
    chunks = chunk_text(text, merge)
    truncated = len(chunks) > MAX_PASSAGES
    chunks = chunks[:MAX_PASSAGES]
    source = f"doc: {filename[:60]}"
    return [{"text": c, "source": source, "external_ref": r} for c, r in zip(chunks, refs(filename, chunks))], truncated
