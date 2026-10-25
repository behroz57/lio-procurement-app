import io

from pypdf import PdfReader


def extract_text(pdf_bytes: bytes) -> str:
    """Extract the text from a vendor-offer PDF.

    Covers the offer formats we handle; the vendors we work with send
    text-based PDFs.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    # Layout mode keeps the column positions, so table rows stay on one line.
    pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    return "\n".join(pages)
