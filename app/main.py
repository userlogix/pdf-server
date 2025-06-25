from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from enum import Enum
from typing import List, Optional
import uuid, os, shutil, base64, zipfile
from datetime import datetime, timedelta
from pathlib import Path
from app.utils import compress_pdf, download_pdf, generate_temp_url, validate_api_key

# Branded, clean FastAPI Swagger
app = FastAPI(
    title="Zeal PDF Utility",
    description="Comprehensive PDF toolkit: compress, trim, merge, split, watermark, password protect and more.",
    version="2.0.0"
)

TEMP_DIR = "/tmp/pdfcache"
os.makedirs(TEMP_DIR, exist_ok=True)

app.mount("/temp", StaticFiles(directory=TEMP_DIR), name="temp")

class CompressionLevel(str, Enum):
    screen = "screen"
    ebook = "ebook"
    printer = "printer"
    prepress = "prepress"

class WatermarkPosition(str, Enum):
    center = "center"
    top_left = "top_left"
    top_right = "top_right"
    bottom_left = "bottom_left"
    bottom_right = "bottom_right"

def get_file_stats(input_path, output_path):
    """Generate file size statistics"""
    original_size = os.path.getsize(input_path)
    compressed_size = os.path.getsize(output_path)
    
    original_kb = original_size / 1024
    compressed_kb = compressed_size / 1024
    original_mb = original_kb / 1024
    compressed_mb = compressed_kb / 1024
    saved_mb = original_mb - compressed_mb
    reduction_pct = round((saved_mb / original_mb) * 100, 2) if original_mb > 0 else 0

    return {
        "original_size_kb": round(original_kb, 2),
        "processed_size_kb": round(compressed_kb, 2),
        "original_size_mb": round(original_mb, 2),
        "processed_size_mb": round(compressed_mb, 2),
        "mb_saved": round(saved_mb, 2),
        "percent_reduction": reduction_pct
    }

def return_file_response(output_path, return_type, filename="processed.pdf", input_path=None):
    """Standard file return handler"""
    stats = get_file_stats(input_path, output_path) if input_path else {}
    
    if return_type == "binary":
        return FileResponse(output_path, media_type="application/pdf", filename=filename)
    
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
            "filename": filename,
            "content_type": "application/pdf",
            "content_base64": content,
            **stats
        }
    
    raise HTTPException(status_code=400, detail="Invalid return_type")

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
    return return_file_response(output_path, return_type, "compressed.pdf", input_path)

@app.post("/trim")
async def trim_pdf(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    start_page: int = Form(..., description="Starting page number (1-indexed)"),
    end_page: int = Form(..., description="Ending page number (1-indexed)"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)

    if not file and not file_url:
        raise HTTPException(status_code=400, detail="Send file or file_url")

    if start_page < 1 or end_page < start_page:
        raise HTTPException(status_code=400, detail="Invalid page range")

    input_path = os.path.join(TEMP_DIR, f"in_{uuid.uuid4()}.pdf")
    output_path = input_path.replace("in_", "out_")

    # Save file or download
    if file:
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    else:
        download_pdf(file_url, input_path)

    try:
        from PyPDF2 import PdfReader, PdfWriter
        reader = PdfReader(input_path)
        total_pages = len(reader.pages)
        
        if start_page > total_pages:
            raise HTTPException(status_code=400, detail=f"Start page {start_page} exceeds total pages {total_pages}")
        
        writer = PdfWriter()
        end_idx = min(end_page, total_pages)
        
        for i in range(start_page - 1, end_idx):
            writer.add_page(reader.pages[i])
        
        with open(output_path, "wb") as f:
            writer.write(f)
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF trim error: {str(e)}")

    return return_file_response(output_path, return_type, "trimmed.pdf", input_path)

@app.post("/merge")
async def merge_pdfs(
    request: Request,
    files: List[UploadFile] = File(None),
    file_urls: str = Form(None, description="Comma-separated URLs"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)

    urls = []
    if file_urls:
        urls = [url.strip() for url in file_urls.split(",") if url.strip()]
    
    if not files and not urls:
        raise HTTPException(status_code=400, detail="Send files or file_urls")
    
    if not files:
        files = []

    output_path = os.path.join(TEMP_DIR, f"merged_{uuid.uuid4()}.pdf")
    input_paths = []

    try:
        from PyPDF2 import PdfWriter, PdfReader
        writer = PdfWriter()

        # Process uploaded files
        for file in files:
            input_path = os.path.join(TEMP_DIR, f"merge_in_{uuid.uuid4()}.pdf")
            input_paths.append(input_path)
            with open(input_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
            
            reader = PdfReader(input_path)
            for page in reader.pages:
                writer.add_page(page)

        # Process URLs
        for url in urls:
            input_path = os.path.join(TEMP_DIR, f"merge_url_{uuid.uuid4()}.pdf")
            input_paths.append(input_path)
            download_pdf(url, input_path)
            
            reader = PdfReader(input_path)
            for page in reader.pages:
                writer.add_page(page)

        with open(output_path, "wb") as f:
            writer.write(f)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF merge error: {str(e)}")
    finally:
        # Cleanup input files
        for path in input_paths:
            if os.path.exists(path):
                os.remove(path)

    return return_file_response(output_path, return_type, "merged.pdf")

@app.post("/split")
async def split_pdf(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    split_range: str = Form(None, description="Optional: comma-separated pages/ranges (e.g., '1,3-5,7')"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)

    if not file and not file_url:
        raise HTTPException(status_code=400, detail="Send file or file_url")

    input_path = os.path.join(TEMP_DIR, f"in_{uuid.uuid4()}.pdf")

    # Save file or download
    if file:
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    else:
        download_pdf(file_url, input_path)

    try:
        from PyPDF2 import PdfReader, PdfWriter
        reader = PdfReader(input_path)
        total_pages = len(reader.pages)
        
        pages_to_extract = []
        
        if split_range:
            # Parse range like "1,3-5,7"
            for part in split_range.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    pages_to_extract.extend(range(start, end + 1))
                else:
                    pages_to_extract.append(int(part))
        else:
            # Split all pages
            pages_to_extract = list(range(1, total_pages + 1))
        
        # Validate pages
        pages_to_extract = [p for p in pages_to_extract if 1 <= p <= total_pages]
        
        if len(pages_to_extract) == 1:
            # Single page - return as PDF
            writer = PdfWriter()
            writer.add_page(reader.pages[pages_to_extract[0] - 1])
            output_path = os.path.join(TEMP_DIR, f"split_{uuid.uuid4()}.pdf")
            with open(output_path, "wb") as f:
                writer.write(f)
            return return_file_response(output_path, return_type, f"page_{pages_to_extract[0]}.pdf", input_path)
        
        else:
            # Multiple pages - create ZIP
            zip_path = os.path.join(TEMP_DIR, f"split_{uuid.uuid4()}.zip")
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for page_num in pages_to_extract:
                    writer = PdfWriter()
                    writer.add_page(reader.pages[page_num - 1])
                    
                    page_path = os.path.join(TEMP_DIR, f"temp_page_{page_num}.pdf")
                    with open(page_path, "wb") as f:
                        writer.write(f)
                    
                    zipf.write(page_path, f"page_{page_num}.pdf")
                    os.remove(page_path)
            
            if return_type == "binary":
                return FileResponse(zip_path, media_type="application/zip", filename="split_pages.zip")
            elif return_type == "url":
                url, expires = generate_temp_url(zip_path)
                return {
                    "url": url,
                    "expires_at": expires.isoformat(),
                    "pages_extracted": len(pages_to_extract)
                }
            elif return_type == "base64":
                with open(zip_path, "rb") as f:
                    content = base64.b64encode(f.read()).decode()
                return {
                    "filename": "split_pages.zip",
                    "content_type": "application/zip",
                    "content_base64": content,
                    "pages_extracted": len(pages_to_extract)
                }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF split error: {str(e)}")

@app.post("/watermark")
async def add_watermark(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    text: str = Form(..., description="Watermark text"),
    opacity: float = Form(0.3, description="Opacity (0.0 to 1.0)"),
    position: WatermarkPosition = Form(WatermarkPosition.center, description="Watermark position"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)

    if not file and not file_url:
        raise HTTPException(status_code=400, detail="Send file or file_url")

    if not 0.0 <= opacity <= 1.0:
        raise HTTPException(status_code=400, detail="Opacity must be between 0.0 and 1.0")

    input_path = os.path.join(TEMP_DIR, f"in_{uuid.uuid4()}.pdf")
    output_path = input_path.replace("in_", "out_")

    # Save file or download
    if file:
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    else:
        download_pdf(file_url, input_path)

    try:
        from PyPDF2 import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.colors import gray
        from io import BytesIO
        
        reader = PdfReader(input_path)
        writer = PdfWriter()
        
        for page in reader.pages:
            # Create watermark
            packet = BytesIO()
            c = canvas.Canvas(packet, pagesize=letter)
            c.setFillColor(gray, alpha=opacity)
            c.setFont("Helvetica", 50)
            
            # Position watermark
            if position == "center":
                x, y = 200, 400
            elif position == "top_left":
                x, y = 50, 750
            elif position == "top_right":
                x, y = 400, 750
            elif position == "bottom_left":
                x, y = 50, 50
            elif position == "bottom_right":
                x, y = 400, 50
            
            c.drawString(x, y, text)
            c.save()
            
            # Apply watermark
            packet.seek(0)
            watermark = PdfReader(packet)
            page.merge_page(watermark.pages[0])
            writer.add_page(page)
        
        with open(output_path, "wb") as f:
            writer.write(f)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Watermark error: {str(e)}")

    return return_file_response(output_path, return_type, "watermarked.pdf", input_path)

@app.post("/password-protect")
async def password_protect(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    password: str = Form(..., description="Password to protect the PDF"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
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

    try:
        from PyPDF2 import PdfReader, PdfWriter
        reader = PdfReader(input_path)
        writer = PdfWriter()
        
        for page in reader.pages:
            writer.add_page(page)
        
        writer.encrypt(password)
        
        with open(output_path, "wb") as f:
            writer.write(f)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Password protection error: {str(e)}")

    return return_file_response(output_path, return_type, "protected.pdf", input_path)

@app.post("/password-remove")
async def password_remove(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    password: str = Form(..., description="Current password to decrypt the PDF"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
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

    try:
        from PyPDF2 import PdfReader, PdfWriter
        reader = PdfReader(input_path)
        
        if reader.is_encrypted:
            if not reader.decrypt(password):
                raise HTTPException(status_code=400, detail="Incorrect password")
        
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        
        with open(output_path, "wb") as f:
            writer.write(f)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Password removal error: {str(e)}")

    return return_file_response(output_path, return_type, "unlocked.pdf", input_path)

@app.delete("/delete")
async def delete_files(
    request: Request,
    filenames: str = Form(..., description="Comma-separated list of filenames to delete")
):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)
    
    files_to_delete = [f.strip() for f in filenames.split(",") if f.strip()]
    deleted = []
    not_found = []
    
    for filename in files_to_delete:
        file_path = os.path.join(TEMP_DIR, filename)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                deleted.append(filename)
            except Exception as e:
                not_found.append({"filename": filename, "error": str(e)})
        else:
            not_found.append({"filename": filename, "error": "File not found"})
    
    return {
        "deleted": deleted,
        "not_found": not_found,
        "total_deleted": len(deleted)
    }

@app.delete("/clear-cache")
async def clear_cache(
    request: Request,
    older_than_minutes: Optional[int] = Form(None, description="Only delete files older than X minutes")
):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)
    
    folder = Path(TEMP_DIR)
    deleted = []
    errors = []
    cutoff_time = None
    
    if older_than_minutes:
        cutoff_time = datetime.now().timestamp() - (older_than_minutes * 60)
    
    for file_path in folder.glob("*"):
        try:
            if cutoff_time and file_path.stat().st_mtime > cutoff_time:
                continue
                
            file_path.unlink()
            deleted.append(file_path.name)
        except Exception as e:
            errors.append({"filename": file_path.name, "error": str(e)})
    
    return {
        "deleted": deleted,
        "errors": errors,
        "total_deleted": len(deleted),
        "filter": f"Files older than {older_than_minutes} minutes" if older_than_minutes else "All files"
    }

# Cache status endpoint
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
        "total_size_kb": round(sum(f["size_kb"] for f in files), 2),
        "files": sorted(files, key=lambda x: x['age_minutes'], reverse=True)
    }