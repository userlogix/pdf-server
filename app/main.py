from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from enum import Enum
import uuid, os, shutil, base64
from datetime import datetime, timedelta
from app.utils import compress_pdf, download_pdf, generate_temp_url, validate_api_key

# Branded, clean FastAPI Swagger
app = FastAPI(
    title="Zeal PDF Utility",
    description="Compress and trim PDFs with optional quality level and return type.",
    version="1.0.0"
)

TEMP_DIR = "/tmp/pdfcache"
os.makedirs(TEMP_DIR, exist_ok=True)

app.mount("/temp", StaticFiles(directory=TEMP_DIR), name="temp")

class CompressionLevel(str, Enum):
    screen = "screen"
    ebook = "ebook"
    printer = "printer"
    prepress = "prepress"

@app.post("/compress")
async def compress(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url"),
    max_pages: int = Form(None, description="Optional: limit PDF to X pages"),
    compression_level: CompressionLevel = Form(CompressionLevel.ebook, description="Compression quality: screen, ebook, printer, prepress")
):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)

    if not file and not file_url:
        raise HTTPException(status_code=400, detail="Send file or file_url")

    input_path = os.path.join(TEMP_DIR, f"in_{uuid.uuid4()}.pdf")
    output_path = input_path.replace("in_", "out_")

    # Save file or download
    if file:
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    else:
        download_pdf(file_url, input_path)

    original_size = os.path.getsize(input_path)

    # Trim PDF if max_pages is passed
    if max_pages:
        from PyPDF2 import PdfReader, PdfWriter
        reader = PdfReader(input_path)
        writer = PdfWriter()
        for i in range(min(max_pages, len(reader.pages))):
            writer.add_page(reader.pages[i])
        with open(input_path, "wb") as f:
            writer.write(f)

    compress_pdf(input_path, output_path, compression_level=compression_level)
    compressed_size = os.path.getsize(output_path)

    # Enhanced stats
    original_kb = original_size / 1024
    compressed_kb = compressed_size / 1024
    original_mb = original_kb / 1024
    compressed_mb = compressed_kb / 1024
    saved_mb = original_mb - compressed_mb
    reduction_pct = round((saved_mb / original_mb) * 100, 2) if original_mb > 0 else 0

    stats = {
        "original_size_kb": round(original_kb, 2),
        "compressed_size_kb": round(compressed_kb, 2),
        "original_size_mb": round(original_mb, 2),
        "compressed_size_mb": round(compressed_mb, 2),
        "mb_saved": round(saved_mb, 2),
        "percent_reduction": reduction_pct
    }

    if return_type == "binary":
        return FileResponse(output_path, media_type="application/pdf", filename="compressed.pdf")

    elif return_type == "url":
        url, expires = generate_temp_url(output_path)
        return {
            "url": url,
            "expires_at": expires.isoformat(),
            **stats
        }

    elif return_type == "base64":
        with open(output_path, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        return {
            "filename": "compressed.pdf",
            "content_type": "application/pdf",
            "content_base64": content,
            **stats
        }

    raise HTTPException(status_code=400, detail="Invalid return_type")


# Cache status endpoint
from pathlib import Path
from datetime import timezone

@app.get("/cache/status")
def cache_status(request: Request):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)

    folder = Path(TEMP_DIR)
    files = []

    for file in folder.glob("*"):
        stat = file.stat()
        created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        age_minutes = round((datetime.now(timezone.utc) - created_at).total_seconds() / 60, 2)
        files.append({
            "filename": file.name,
            "size_kb": round(stat.st_size / 1024, 2),
            "created_at": created_at.isoformat(),
            "age_minutes": age_minutes
        })

    return {
        "file_count": len(files),
        "files": sorted(files, key=lambda x: x['age_minutes'], reverse=True)
    }