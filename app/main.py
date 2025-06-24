from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uuid, os, shutil, base64
from datetime import datetime, timedelta
from app.utils import compress_pdf, download_pdf, generate_temp_url, validate_api_key

# Custom FastAPI branding
app = FastAPI(
    title="Zeal PDF Utility",
    description="A lightweight API to compress PDFs, trim pages, and return results as base64, binary, or temporary URL.",
    version="1.0.0",
    terms_of_service="https://byzeal.com/terms",
    contact={
        "name": "Zeal Support",
        "email": "support@byzeal.com",
        "url": "https://internalsupport.byzeal.com",
    },
    license_info={
        "name": "MIT",
        "url": "https://opensource.org/licenses/MIT",
    }
)

TEMP_DIR = "/tmp/pdfcache"
os.makedirs(TEMP_DIR, exist_ok=True)

app.mount("/temp", StaticFiles(directory=TEMP_DIR), name="temp")

@app.post("/compress")
async def compress(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    return_type: str = Form("base64"),
    max_pages: int = Form(None)
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

    compress_pdf(input_path, output_path)
    compressed_size = os.path.getsize(output_path)

    if return_type == "binary":
        return FileResponse(output_path, media_type="application/pdf", filename="compressed.pdf")

    elif return_type == "url":
        url, expires = generate_temp_url(output_path)
        return {
            "url": url,
            "expires_at": expires.isoformat(),
            "original_size_kb": round(original_size / 1024, 2),
            "compressed_size_kb": round(compressed_size / 1024, 2)
        }

    elif return_type == "base64":
        with open(output_path, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        return {
            "filename": "compressed.pdf",
            "content_type": "application/pdf",
            "original_size_kb": round(original_size / 1024, 2),
            "compressed_size_kb": round(compressed_size / 1024, 2),
            "content_base64": content
        }

    raise HTTPException(status_code=400, detail="Invalid return_type")