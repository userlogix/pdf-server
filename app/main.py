from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from enum import Enum
from typing import List, Optional
import uuid, os, shutil, base64, zipfile, subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app.utils import compress_pdf, download_pdf, generate_temp_url, validate_api_key
import asyncio
import logging
import magic
import requests

# Branded, clean FastAPI Swagger
app = FastAPI(
    title="Zeal PDF Utility",
    description="Comprehensive PDF toolkit: compress, trim, merge, split, watermark, password protect and more.",
    version="2.0.0",
    openapi_tags=[
        {
            "name": "Core PDF Operations",
            "description": "Essential PDF manipulation: compress, trim, merge, split"
        },
        {
            "name": "Document Conversion", 
            "description": "Convert any document type to PDF"
        },
        {
            "name": "PDF Enhancement",
            "description": "Add features to existing PDFs: watermarks, passwords, page numbers"
        },
        {
            "name": "Text & OCR",
            "description": "Extract text and make PDFs searchable"
        },
        {
            "name": "Advanced Operations",
            "description": "Complex workflows and bulk operations"
        },
        {
            "name": "Cache Management",
            "description": "Monitor and manage temporary files"
        }
    ]
)

TEMP_DIR = "/tmp/pdfcache"
os.makedirs(TEMP_DIR, exist_ok=True)

app.mount("/temp", StaticFiles(directory=TEMP_DIR), name="temp")

# Cleanup configuration
CLEANUP_MODE = os.environ.get("CLEANUP_MODE", "lazy").lower()  # "lazy" or "active"
CLEANUP_INTERVAL_MINUTES = int(os.environ.get("CLEANUP_INTERVAL_MINUTES", "30"))
FILE_EXPIRATION_MINUTES = int(os.environ.get("FILE_EXPIRATION_MINUTES", "60"))

# Global variable to track active cleanup task
cleanup_task = None

def cleanup_expired_files():
    """Remove files older than FILE_EXPIRATION_MINUTES"""
    try:
        folder = Path(TEMP_DIR)
        cutoff_time = datetime.now().timestamp() - (FILE_EXPIRATION_MINUTES * 60)
        deleted_count = 0
        
        for file_path in folder.glob("*"):
            try:
                if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                    file_path.unlink()
                    deleted_count += 1
            except Exception as e:
                logging.warning(f"Failed to delete {file_path}: {str(e)}")
        
        if deleted_count > 0:
            logging.info(f"Cleanup: Deleted {deleted_count} expired files")
            
        return deleted_count
    except Exception as e:
        logging.error(f"Cleanup error: {str(e)}")
        return 0

async def active_cleanup_loop():
    """Background cleanup loop for active mode"""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_MINUTES * 60)  # Convert to seconds
            cleanup_expired_files()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"Active cleanup loop error: {str(e)}")

def lazy_cleanup():
    """Run cleanup on-demand for lazy mode"""
    if CLEANUP_MODE == "lazy":
        cleanup_expired_files()

@app.on_event("startup")
async def startup_event():
    """Initialize cleanup based on mode"""
    global cleanup_task
    
    if CLEANUP_MODE == "active":
        cleanup_task = asyncio.create_task(active_cleanup_loop())
        logging.info(f"Active cleanup started: Every {CLEANUP_INTERVAL_MINUTES} minutes, deleting files older than {FILE_EXPIRATION_MINUTES} minutes")
    else:
        logging.info(f"Lazy cleanup enabled: Files older than {FILE_EXPIRATION_MINUTES} minutes deleted on each request")

@app.on_event("shutdown")
async def shutdown_event():
    """Stop active cleanup task"""
    global cleanup_task
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

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

@app.post("/compress", tags=["Core PDF Operations"])
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
    
    # Lazy cleanup on each request
    lazy_cleanup()

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

@app.post("/trim", tags=["Core PDF Operations"])
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
    
    # Lazy cleanup on each request
    lazy_cleanup()

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

@app.post("/merge", tags=["Core PDF Operations"])
async def merge_pdfs(
    request: Request,
    files: List[UploadFile] = File(None),
    file_urls: str = Form(None, description="Comma-separated URLs"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)
    
    # Lazy cleanup on each request
    lazy_cleanup()

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

@app.post("/split", tags=["Core PDF Operations"])
async def split_pdf(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    split_range: str = Form(None, description="Optional: comma-separated pages/ranges (e.g., '1,3-5,7')"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)
    
    # Lazy cleanup on each request
    lazy_cleanup()

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

@app.post("/watermark", tags=["PDF Enhancement"])
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
    
    # Lazy cleanup on each request
    lazy_cleanup()

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

@app.post("/password-protect", tags=["PDF Enhancement"])
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

@app.post("/password-remove", tags=["PDF Enhancement"])
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

@app.post("/convert-to-pdf", tags=["Document Conversion"])
async def convert_to_pdf(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    title: str = Form(None, description="Optional title for image conversions"),
    fit_to_letter: bool = Form(False, description="For images: resize to fit letter size"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
):
    """Universal document to PDF converter - auto-detects file type"""
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)
    
    # Lazy cleanup on each request
    lazy_cleanup()

    if not file and not file_url:
        raise HTTPException(status_code=400, detail="Send file or file_url")

    input_path = os.path.join(TEMP_DIR, f"convert_{uuid.uuid4()}")
    output_path = os.path.join(TEMP_DIR, f"converted_{uuid.uuid4()}.pdf")

    try:
        # Save/download file
        if file:
            filename = file.filename or "document"
            file_ext = filename.split(".")[-1].lower() if "." in filename else ""
            input_path += f".{file_ext}" if file_ext else ""
            
            with open(input_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
        else:
            # Download file
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(file_url, stream=True, headers=headers, timeout=60)
            if r.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Failed to download file: {r.status_code}")
            
            # Try to determine extension from URL or content-type
            url_ext = file_url.split(".")[-1].lower() if "." in file_url else ""
            content_type = r.headers.get("Content-Type", "")
            
            if url_ext in ["pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "txt", "rtf", "odt", "jpg", "jpeg", "png"]:
                input_path += f".{url_ext}"
            elif "pdf" in content_type:
                input_path += ".pdf"
            elif "word" in content_type or "document" in content_type:
                input_path += ".docx"
            elif "excel" in content_type or "spreadsheet" in content_type:
                input_path += ".xlsx"
            elif "powerpoint" in content_type or "presentation" in content_type:
                input_path += ".pptx"
            elif "image" in content_type:
                if "jpeg" in content_type:
                    input_path += ".jpg"
                elif "png" in content_type:
                    input_path += ".png"
                else:
                    input_path += ".jpg"
            
            with open(input_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

        # Detect file type and convert
        mime_type = magic.from_file(input_path, mime=True)
        file_extension = input_path.split(".")[-1].lower() if "." in input_path else ""
        
        # Already PDF - just copy
        if mime_type == "application/pdf" or file_extension == "pdf":
            shutil.copy2(input_path, output_path)
            
        # Images - use existing image-to-pdf logic
        elif mime_type.startswith("image/") or file_extension in ["jpg", "jpeg", "png", "gif", "bmp", "tiff"]:
            from reportlab.pdfgen import canvas
            from reportlab.lib.pagesizes import letter
            from PIL import Image
            
            img = Image.open(input_path)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            page_width, page_height = letter
            title_height = 60 if title else 20
            available_height = page_height - title_height - 40
            available_width = page_width - 40
            
            if fit_to_letter:
                img_width, img_height = img.size
                scale_w = available_width / img_width
                scale_h = available_height / img_height
                scale = min(scale_w, scale_h)
                new_width = img_width * scale
                new_height = img_height * scale
            else:
                new_width, new_height = img.size
            
            c = canvas.Canvas(output_path, pagesize=letter)
            
            if title:
                c.setFont("Helvetica-Bold", 16)
                c.drawCentredText(page_width / 2, page_height - 40, title)
            
            x = (page_width - new_width) / 2
            y = (available_height - new_height) / 2 + 20
            c.drawImage(input_path, x, y, width=new_width, height=new_height)
            c.save()
            
        # Office documents and others - use LibreOffice
        elif (mime_type in [
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
            "application/msword",  # doc
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
            "application/vnd.ms-excel",  # xls
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # pptx
            "application/vnd.ms-powerpoint",  # ppt
            "application/vnd.oasis.opendocument.text",  # odt
            "application/rtf",  # rtf
            "text/plain"  # txt
        ] or file_extension in ["docx", "doc", "xlsx", "xls", "pptx", "ppt", "odt", "rtf", "txt"]):
            
            # Use LibreOffice to convert
            
            # Create temp directory for LibreOffice output
            temp_dir = os.path.dirname(input_path)
            
            result = subprocess.run([
                "libreoffice", "--headless", "--convert-to", "pdf",
                "--outdir", temp_dir, input_path
            ], capture_output=True, text=True, timeout=120)
            
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"LibreOffice conversion failed: {result.stderr}")
            
            # Find the converted PDF
            base_name = os.path.splitext(os.path.basename(input_path))[0]
            converted_pdf = os.path.join(temp_dir, f"{base_name}.pdf")
            
            if not os.path.exists(converted_pdf):
                raise HTTPException(status_code=500, detail="Conversion completed but PDF not found")
            
            shutil.move(converted_pdf, output_path)
            
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {mime_type}")
        
        # Cleanup input file
        if os.path.exists(input_path):
            os.remove(input_path)

    except Exception as e:
        # Cleanup on error
        if os.path.exists(input_path):
            os.remove(input_path)
        if "subprocess" in str(e) or "LibreOffice" in str(e):
            raise HTTPException(status_code=500, detail=f"Document conversion error: {str(e)}")
        else:
            raise HTTPException(status_code=500, detail=f"Conversion error: {str(e)}")

    return return_file_response(output_path, return_type, "converted.pdf")

@app.post("/make-searchable", tags=["Text & OCR"])
async def make_pdf_searchable(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    language: str = Form("eng", description="OCR language (eng, spa, fra, deu, etc.)"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
):
    """Convert image-based PDF to searchable PDF by adding invisible OCR text layer"""
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)
    
    # Lazy cleanup on each request
    lazy_cleanup()

    if not file and not file_url:
        raise HTTPException(status_code=400, detail="Send file or file_url")

    input_path = os.path.join(TEMP_DIR, f"in_{uuid.uuid4()}.pdf")
    output_path = input_path.replace("in_", "searchable_")

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
        from reportlab.lib.colors import Color
        import pytesseract
        from PIL import Image
        import pdf2image
        from io import BytesIO
        
        reader = PdfReader(input_path)
        writer = PdfWriter()
        
        for page_num, original_page in enumerate(reader.pages, 1):
            # Convert PDF page to image for OCR
            try:
                images = pdf2image.convert_from_path(
                    input_path, 
                    first_page=page_num, 
                    last_page=page_num,
                    dpi=300  # Higher DPI for better OCR
                )
                
                if not images:
                    # No image found, just add original page
                    writer.add_page(original_page)
                    continue
                
                image = images[0]
                
                # Run OCR with position data
                ocr_data = pytesseract.image_to_data(
                    image, 
                    lang=language,
                    output_type=pytesseract.Output.DICT
                )
                
                # Create invisible text overlay
                packet = BytesIO()
                page_width = float(original_page.mediabox.width)
                page_height = float(original_page.mediabox.height)
                
                c = canvas.Canvas(packet, pagesize=(page_width, page_height))
                
                # Add invisible text at OCR coordinates
                image_width, image_height = image.size
                
                for i in range(len(ocr_data['text'])):
                    text = ocr_data['text'][i].strip()
                    if text and int(ocr_data['conf'][i]) > 30:  # Only use confident OCR results
                        # Convert image coordinates to PDF coordinates
                        x = float(ocr_data['left'][i]) * page_width / image_width
                        y = page_height - (float(ocr_data['top'][i]) * page_height / image_height)
                        width = float(ocr_data['width'][i]) * page_width / image_width
                        height = float(ocr_data['height'][i]) * page_height / image_height
                        
                        # Make text invisible (white text on white background)
                        c.setFillColor(Color(1, 1, 1, alpha=0))  # Transparent
                        c.setFont("Helvetica", max(8, height * 0.8))  # Size based on OCR box height
                        
                        # Draw invisible text
                        c.drawString(x, y - height, text)
                
                c.save()
                
                # Merge invisible text layer with original page
                packet.seek(0)
                text_overlay = PdfReader(packet)
                
                if text_overlay.pages:
                    original_page.merge_page(text_overlay.pages[0])
                
                writer.add_page(original_page)
                
            except Exception as ocr_error:
                # OCR failed for this page, just add original
                print(f"OCR failed for page {page_num}: {str(ocr_error)}")
                writer.add_page(original_page)
                continue
        
        # Write the searchable PDF
        with open(output_path, "wb") as f:
            writer.write(f)
        
        # Cleanup input file
        if os.path.exists(input_path):
            os.remove(input_path)

    except Exception as e:
        # Cleanup on error
        if os.path.exists(input_path):
            os.remove(input_path)
        raise HTTPException(status_code=500, detail=f"Searchable PDF creation error: {str(e)}")

    return return_file_response(output_path, return_type, "searchable.pdf")

@app.post("/merge-with-bookmarks", tags=["Advanced Operations"], 
         summary="Merge PDFs with automatic bookmark creation",
         description="Merge multiple PDFs and create navigation bookmarks for each document. Perfect for creating organized document packages.")
async def merge_with_bookmarks(
    request: Request,
    files: List[UploadFile] = File(None),
    file_urls: str = Form(None, description="Comma-separated URLs"),
    titles: str = Form(..., description="Comma-separated bookmark titles (must match file order)"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)
    
    # Lazy cleanup on each request
    lazy_cleanup()

    # Parse titles
    title_list = [t.strip() for t in titles.split(",") if t.strip()]
    
    # Parse URLs if provided
    urls = []
    if file_urls:
        urls = [url.strip() for url in file_urls.split(",") if url.strip()]
    
    if not files and not urls:
        raise HTTPException(status_code=400, detail="Send files or file_urls")
    
    if not files:
        files = []
    
    # Validate that we have the same number of titles as files
    total_files = len(files) + len(urls)
    if len(title_list) != total_files:
        raise HTTPException(status_code=400, detail=f"Number of titles ({len(title_list)}) must match number of files ({total_files})")

    output_path = os.path.join(TEMP_DIR, f"bookmarked_{uuid.uuid4()}.pdf")
    input_paths = []

    try:
        from PyPDF2 import PdfWriter, PdfReader
        writer = PdfWriter()
        current_page = 0
        
        # Process uploaded files
        for i, file in enumerate(files):
            input_path = os.path.join(TEMP_DIR, f"bookmark_in_{uuid.uuid4()}.pdf")
            input_paths.append(input_path)
            with open(input_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
            
            reader = PdfReader(input_path)
            
            # Add bookmark at current page
            writer.add_outline_item(title_list[i], current_page)
            
            # Add all pages from this file
            for page in reader.pages:
                writer.add_page(page)
                current_page += 1

        # Process URLs
        url_start_index = len(files)
        for i, url in enumerate(urls):
            input_path = os.path.join(TEMP_DIR, f"bookmark_url_{uuid.uuid4()}.pdf")
            input_paths.append(input_path)
            download_pdf(url, input_path)
            
            reader = PdfReader(input_path)
            title_index = url_start_index + i
            
            # Add bookmark at current page
            writer.add_outline_item(title_list[title_index], current_page)
            
            # Add all pages from this file
            for page in reader.pages:
                writer.add_page(page)
                current_page += 1

        with open(output_path, "wb") as f:
            writer.write(f)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bookmark merge error: {str(e)}")
    finally:
        # Cleanup input files
        for path in input_paths:
            if os.path.exists(path):
                os.remove(path)

    return return_file_response(output_path, return_type, "bookmarked.pdf")

@app.post("/prepare-document", tags=["Advanced Operations"],
         summary="Comprehensive document preparation pipeline",
         description="One-stop endpoint: converts any document to optimized, letter-size, unprotected PDF. Handles orientation detection and forced password removal.")
async def prepare_document(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    filename: str = Form(None, description="Optional: Custom filename for the output (without extension)"),
    force_password_removal: bool = Form(True, description="Attempt password removal without knowing password"),
    target_compression: CompressionLevel = Form(CompressionLevel.ebook, description="Target compression level"),
    max_file_size_mb: float = Form(8.0, description="Target maximum file size in MB"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
):
    """
    Comprehensive document preparation pipeline that:
    1. Converts any document type to PDF
    2. Attempts forced password removal
    3. Intelligently analyzes and resizes to letter size (only when needed)
    4. Optimizes file size with progressive compression
    5. Returns processing metadata with detailed logging
    
    Always ensures letter-size output with robust error handling.
    """
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)
    
    lazy_cleanup()

    if not file and not file_url:
        raise HTTPException(status_code=400, detail="Send file or file_url")

    # Initialize file paths
    input_path = os.path.join(TEMP_DIR, f"prepare_in_{uuid.uuid4()}")
    working_path = os.path.join(TEMP_DIR, f"prepare_work_{uuid.uuid4()}.pdf")
    output_path = os.path.join(TEMP_DIR, f"prepare_out_{uuid.uuid4()}.pdf")
    
    processing_log = []
    is_password_protected = False
    original_size = 0

    try:
        # Step 1: Download/save and detect file type
        processing_log.append("Starting document preparation")
        if filename:
            processing_log.append(f"Output filename will be: {filename}.pdf")
        
        if file:
            filename = file.filename or "document"
            file_ext = filename.split(".")[-1].lower() if "." in filename else ""
            input_path += f".{file_ext}" if file_ext else ""
            
            with open(input_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
        else:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(file_url, stream=True, headers=headers, timeout=60)
            if r.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Failed to download file: {r.status_code}")
            
            # Auto-detect extension
            url_ext = file_url.split(".")[-1].lower() if "." in file_url else ""
            content_type = r.headers.get("Content-Type", "")
            
            if url_ext in ["pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "txt", "rtf", "odt", "jpg", "jpeg", "png"]:
                input_path += f".{url_ext}"
            elif "pdf" in content_type:
                input_path += ".pdf"
            elif "word" in content_type or "document" in content_type:
                input_path += ".docx"
            elif "excel" in content_type or "spreadsheet" in content_type:
                input_path += ".xlsx"
            elif "powerpoint" in content_type or "presentation" in content_type:
                input_path += ".pptx"
            elif "image" in content_type:
                if "jpeg" in content_type:
                    input_path += ".jpg"
                elif "png" in content_type:
                    input_path += ".png"
                else:
                    input_path += ".jpg"
            
            with open(input_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        
        original_size = os.path.getsize(input_path)
        processing_log.append(f"Input file size: {original_size / 1024 / 1024:.2f} MB")

        # Step 2: Convert to PDF if needed
        mime_type = magic.from_file(input_path, mime=True)
        file_extension = input_path.split(".")[-1].lower() if "." in input_path else ""
        
        if mime_type == "application/pdf" or file_extension == "pdf":
            shutil.copy2(input_path, working_path)
            processing_log.append("Already PDF - skipped conversion")
        else:
            processing_log.append(f"Converting {mime_type} to PDF")
            
            # Images - use ReportLab
            if mime_type.startswith("image/") or file_extension in ["jpg", "jpeg", "png", "gif", "bmp", "tiff"]:
                from reportlab.pdfgen import canvas
                from reportlab.lib.pagesizes import letter
                from PIL import Image
                
                img = Image.open(input_path)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Smart orientation detection
                img_width, img_height = img.size
                is_landscape = img_width > img_height
                
                if is_landscape:
                    page_size = (letter[1], letter[0])  # Landscape letter
                    processing_log.append("Detected landscape orientation")
                else:
                    page_size = letter  # Portrait letter
                    processing_log.append("Detected portrait orientation")
                
                page_width, page_height = page_size
                available_width = page_width - 40  # margins
                available_height = page_height - 40
                
                # Scale to fit
                scale_w = available_width / img_width
                scale_h = available_height / img_height
                scale = min(scale_w, scale_h)
                
                new_width = img_width * scale
                new_height = img_height * scale
                
                c = canvas.Canvas(working_path, pagesize=page_size)
                x = (page_width - new_width) / 2
                y = (page_height - new_height) / 2
                c.drawImage(input_path, x, y, width=new_width, height=new_height)
                c.save()
                
            # Office documents - use LibreOffice
            elif (mime_type in [
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
                "application/msword",  # doc
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
                "application/vnd.ms-excel",  # xls
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # pptx
                "application/vnd.ms-powerpoint",  # ppt
                "application/vnd.oasis.opendocument.text",  # odt
                "application/rtf",  # rtf
                "text/plain"  # txt
            ] or file_extension in ["docx", "doc", "xlsx", "xls", "pptx", "ppt", "odt", "rtf", "txt"]):
                
                temp_dir = os.path.dirname(input_path)
                
                result = subprocess.run([
                    "libreoffice", "--headless", "--convert-to", "pdf",
                    "--outdir", temp_dir, input_path
                ], capture_output=True, text=True, timeout=120)
                
                if result.returncode != 0:
                    raise HTTPException(status_code=500, detail=f"LibreOffice conversion failed: {result.stderr}")
                
                base_name = os.path.splitext(os.path.basename(input_path))[0]
                converted_pdf = os.path.join(temp_dir, f"{base_name}.pdf")
                
                if not os.path.exists(converted_pdf):
                    raise HTTPException(status_code=500, detail="Conversion completed but PDF not found")
                
                shutil.move(converted_pdf, working_path)
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported file type: {mime_type}")

        # Step 3: Attempt forced password removal
        if force_password_removal:
            try:
                from PyPDF2 import PdfReader, PdfWriter
                temp_path = working_path.replace(".pdf", "_temp.pdf")
                
                reader = PdfReader(working_path)
                if reader.is_encrypted:
                    processing_log.append("PDF is password protected - attempting removal")
                    
                    # Try common passwords first
                    common_passwords = ["", "123456", "password", "admin", "user", "pdf", "document"]
                    decrypted = False
                    
                    for pwd in common_passwords:
                        if reader.decrypt(pwd):
                            decrypted = True
                            processing_log.append(f"Successfully decrypted with common password")
                            break
                    
                    if not decrypted:
                        # Force decryption using ghostscript
                        try:
                            subprocess.run([
                                "gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
                                "-sOutputFile=" + temp_path, "-c", ".setpdfwrite", "-f", working_path
                            ], check=True, capture_output=True)
                            
                            if os.path.exists(temp_path):
                                shutil.move(temp_path, working_path)
                                processing_log.append("Forced password removal successful")
                            else:
                                is_password_protected = True
                                processing_log.append("Failed to remove password protection")
                        except:
                            is_password_protected = True
                            processing_log.append("Failed to remove password protection")
                    else:
                        # Re-save without encryption
                        writer = PdfWriter()
                        for page in reader.pages:
                            writer.add_page(page)
                        
                        with open(temp_path, "wb") as f:
                            writer.write(f)
                        shutil.move(temp_path, working_path)
                else:
                    processing_log.append("PDF is not password protected")
                    
            except Exception as e:
                processing_log.append(f"Password removal error: {str(e)}")

        # Step 4: Intelligent letter size standardization
        try:
            from PyPDF2 import PdfReader
            from reportlab.lib.pagesizes import letter
            
            processing_log.append("Analyzing page dimensions")
            
            reader = PdfReader(working_path)
            letter_width, letter_height = letter  # 612 x 792 points
            
            # Analyze all pages to understand the document
            needs_resize = False
            page_info = []
            portrait_pages = 0
            landscape_pages = 0
            
            for i, page in enumerate(reader.pages):
                page_width = float(page.mediabox.width)
                page_height = float(page.mediabox.height)
                
                # Determine if this page needs resizing (5% tolerance)
                width_diff = abs(page_width - letter_width) / letter_width
                height_diff = abs(page_height - letter_height) / letter_height
                landscape_width_diff = abs(page_width - letter_height) / letter_height
                landscape_height_diff = abs(page_height - letter_width) / letter_width
                
                # Check if it's already letter size (portrait or landscape)
                is_letter_portrait = width_diff < 0.05 and height_diff < 0.05
                is_letter_landscape = landscape_width_diff < 0.05 and landscape_height_diff < 0.05
                is_landscape = page_width > page_height
                
                if is_landscape:
                    landscape_pages += 1
                else:
                    portrait_pages += 1
                
                page_info.append({
                    "page": i + 1,
                    "width": page_width,
                    "height": page_height,
                    "is_landscape": is_landscape,
                    "needs_resize": not (is_letter_portrait or is_letter_landscape)
                })
                
                if not (is_letter_portrait or is_letter_landscape):
                    needs_resize = True
            
            processing_log.append(f"Document analysis: {len(reader.pages)} pages ({portrait_pages} portrait, {landscape_pages} landscape)")
            
            if not needs_resize:
                processing_log.append("All pages are already letter size - skipping resize")
            else:
                pages_needing_resize = sum(1 for p in page_info if p["needs_resize"])
                processing_log.append(f"{pages_needing_resize} pages need resizing to letter size")
                # Use ghostscript for robust resizing instead of PyPDF2
                processing_log.append("Using ghostscript for robust letter-size conversion")
                resize_path = working_path.replace(".pdf", "_resized.pdf")
                
                # Ghostscript command for letter size fitting
                gs_command = [
                    "gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
                    "-sPAPERSIZE=letter", "-dFIXEDMEDIA", "-dPDFFitPage",
                    "-dCompatibilityLevel=1.4",
                    f"-sOutputFile={resize_path}",
                    working_path
                ]
                
                result = subprocess.run(gs_command, capture_output=True, text=True)
                
                if result.returncode == 0 and os.path.exists(resize_path) and os.path.getsize(resize_path) > 1000:
                    shutil.move(resize_path, working_path)
                    processing_log.append("Successfully resized to letter size using ghostscript")
                else:
                    processing_log.append(f"Ghostscript resize failed: {result.stderr} - continuing with original")
                    if os.path.exists(resize_path):
                        os.remove(resize_path)
                
        except Exception as e:
            processing_log.append(f"Page analysis/resize failed: {str(e)} - continuing with original PDF")

        # Verify working file is still valid before compression
        if not os.path.exists(working_path) or os.path.getsize(working_path) < 1000:
            raise HTTPException(status_code=500, detail="PDF became corrupted during processing")

        # Step 5: Progressive compression until target size is reached
        processing_log.append("Starting compression optimization")
        current_path = working_path
        compression_levels = ["screen", "ebook", "printer", "prepress"]
        target_level_index = compression_levels.index(target_compression)
        
        compression_successful = False
        
        for i in range(target_level_index, len(compression_levels)):
            level = compression_levels[i]
            compress_path = working_path.replace(".pdf", f"_compress_{level}.pdf")
            
            try:
                compress_pdf(current_path, compress_path, compression_level=level)
                
                # Check file size and validity
                if os.path.exists(compress_path) and os.path.getsize(compress_path) > 1000:
                    compressed_size = os.path.getsize(compress_path)
                    size_mb = compressed_size / 1024 / 1024
                    
                    processing_log.append(f"Compression {level}: {size_mb:.2f} MB")
                    
                    if size_mb <= max_file_size_mb or i == len(compression_levels) - 1:
                        shutil.move(compress_path, output_path)
                        processing_log.append(f"Final compression: {level}")
                        compression_successful = True
                        break
                    else:
                        current_path = compress_path
                else:
                    processing_log.append(f"Compression {level} produced empty file - trying next level")
                    if os.path.exists(compress_path):
                        os.remove(compress_path)
                    
            except Exception as e:
                processing_log.append(f"Compression {level} failed: {str(e)}")
                if os.path.exists(compress_path):
                    os.remove(compress_path)
                
                if i == target_level_index:
                    # First compression failed, try next level or copy original
                    continue
        
        # If all compression attempts failed, use the original
        if not compression_successful:
            processing_log.append("All compression attempts failed - using original PDF")
            shutil.copy2(current_path, output_path)

        # Cleanup temporary files
        for temp_file in [input_path, working_path]:
            if os.path.exists(temp_file):
                os.remove(temp_file)

        # Final validation - ensure output file exists and is valid
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1000:
            raise HTTPException(status_code=500, detail="Failed to generate valid output PDF")

        # Generate response with comprehensive metadata
        final_size = os.path.getsize(output_path)
        final_size_mb = final_size / 1024 / 1024
        size_reduction = ((original_size - final_size) / original_size * 100) if original_size > 0 else 0

        processing_log.append(f"Processing complete - final size: {final_size_mb:.2f} MB")

        # Use custom filename if provided, otherwise default
        output_filename = f"{filename}.pdf" if filename else "prepared_document.pdf"

        base_response = {
            "original_size_mb": round(original_size / 1024 / 1024, 2),
            "final_size_mb": round(final_size_mb, 2),
            "size_reduction_percent": round(size_reduction, 2),
            "is_password_protected": is_password_protected,
            "processing_log": processing_log,
            "status": "success" if not is_password_protected else "success_with_protection"
        }

        if return_type == "binary":
            return FileResponse(output_path, media_type="application/pdf", filename=output_filename)
        
        elif return_type == "url":
            url, expires = generate_temp_url(output_path)
            return {
                "url": url,
                "expires_at": expires.isoformat(),
                "filename": output_filename,
                **base_response
            }
        
        elif return_type == "base64":
            with open(output_path, "rb") as f:
                content = base64.b64encode(f.read()).decode()
            return {
                "filename": output_filename,
                "content_type": "application/pdf",
                "content_base64": content,
                **base_response
            }

    except Exception as e:
        # Cleanup on error
        for temp_file in [input_path, working_path, output_path]:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        raise HTTPException(status_code=500, detail=f"Document preparation error: {str(e)}")

@app.post("/image-to-pdf", tags=["Document Conversion"])
async def image_to_pdf(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    title: str = Form(None, description="Optional title to add at top of page"),
    fit_to_letter: bool = Form(False, description="Resize image to fit letter size (8.5x11)"),
    return_type: str = Form("base64", description="Choose how the output is returned: base64, binary, or url")
):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)

    if not file and not file_url:
        raise HTTPException(status_code=400, detail="Send file or file_url")

    input_path = os.path.join(TEMP_DIR, f"img_{uuid.uuid4()}")
    output_path = os.path.join(TEMP_DIR, f"out_{uuid.uuid4()}.pdf")

    try:
        # Save image file
        if file:
            file_ext = file.filename.split(".")[-1].lower() if file.filename else "jpg"
            input_path += f".{file_ext}"
            with open(input_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
        else:
            # Download image
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(file_url, stream=True, headers=headers, timeout=60)
            if r.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Failed to download image: {r.status_code}")
            
            # Determine file extension
            content_type = r.headers.get("Content-Type", "")
            if "jpeg" in content_type or "jpg" in content_type:
                file_ext = "jpg"
            elif "png" in content_type:
                file_ext = "png"
            else:
                file_ext = "jpg"  # default
            
            input_path += f".{file_ext}"
            with open(input_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

        # Create PDF
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        from PIL import Image
        
        # Open and process image
        img = Image.open(input_path)
        
        # Convert to RGB if necessary
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Calculate dimensions
        page_width, page_height = letter
        title_height = 60 if title else 20  # Space for title
        available_height = page_height - title_height - 40  # margins
        available_width = page_width - 40  # margins
        
        if fit_to_letter:
            # Calculate scaling to fit within available space
            img_width, img_height = img.size
            scale_w = available_width / img_width
            scale_h = available_height / img_height
            scale = min(scale_w, scale_h)  # Use smaller scale to maintain aspect ratio
            
            new_width = img_width * scale
            new_height = img_height * scale
        else:
            # Use original size (might be clipped)
            new_width, new_height = img.size
        
        # Create PDF
        c = canvas.Canvas(output_path, pagesize=letter)
        
        # Add title if provided
        if title:
            c.setFont("Helvetica-Bold", 16)
            c.drawCentredText(page_width / 2, page_height - 40, title)
        
        # Add image
        x = (page_width - new_width) / 2  # Center horizontally
        y = (available_height - new_height) / 2 + 20  # Center vertically in available space
        
        c.drawImage(input_path, x, y, width=new_width, height=new_height)
        c.save()
        
        # Cleanup input file
        if os.path.exists(input_path):
            os.remove(input_path)

    except Exception as e:
        # Cleanup on error
        if os.path.exists(input_path):
            os.remove(input_path)
        raise HTTPException(status_code=500, detail=f"Image to PDF error: {str(e)}")

    return return_file_response(output_path, return_type, "image_document.pdf")

@app.post("/add-page-numbers", tags=["PDF Enhancement"])
async def add_page_numbers(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    start_page: int = Form(1, description="Page number to start with"),
    skip_first: bool = Form(False, description="Skip numbering the first page"),
    position: str = Form("bottom-center", description="Position: bottom-left, bottom-center, bottom-right"),
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
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        from io import BytesIO
        
        reader = PdfReader(input_path)
        writer = PdfWriter()
        
        for i, page in enumerate(reader.pages):
            # Skip numbering first page if requested
            if skip_first and i == 0:
                writer.add_page(page)
                continue
            
            # Calculate page number
            if skip_first:
                page_num = start_page + i - 1
            else:
                page_num = start_page + i
            
            # Create page number overlay
            packet = BytesIO()
            c = canvas.Canvas(packet, pagesize=letter)
            c.setFont("Helvetica", 10)
            
            # Position page number
            if position == "bottom-left":
                x, y = 50, 30
            elif position == "bottom-center":
                x, y = letter[0] / 2, 30
            elif position == "bottom-right":
                x, y = letter[0] - 50, 30
            else:
                x, y = letter[0] / 2, 30  # default to center
            
            c.drawCentredText(x, y, str(page_num))
            c.save()
            
            # Apply page number overlay
            packet.seek(0)
            number_page = PdfReader(packet)
            page.merge_page(number_page.pages[0])
            writer.add_page(page)
        
        with open(output_path, "wb") as f:
            writer.write(f)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Page numbering error: {str(e)}")

    return return_file_response(output_path, return_type, "numbered.pdf", input_path)

@app.post("/resize-to-letter", tags=["PDF Enhancement"])
async def resize_to_letter(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
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
        from reportlab.lib.pagesizes import letter
        
        reader = PdfReader(input_path)
        writer = PdfWriter()
        
        letter_width, letter_height = letter
        
        for page in reader.pages:
            # Get current page dimensions
            page_width = float(page.mediabox.width)
            page_height = float(page.mediabox.height)
            
            # Calculate scaling factors
            scale_x = letter_width / page_width
            scale_y = letter_height / page_height
            scale = min(scale_x, scale_y)  # Maintain aspect ratio
            
            # Scale the page
            page.scale(scale, scale)
            
            # Center the page on letter size
            new_width = page_width * scale
            new_height = page_height * scale
            x_offset = (letter_width - new_width) / 2
            y_offset = (letter_height - new_height) / 2
            
            # Set new mediabox to letter size
            page.mediabox.lower_left = (x_offset, y_offset)
            page.mediabox.upper_right = (x_offset + new_width, y_offset + new_height)
            
            writer.add_page(page)
        
        with open(output_path, "wb") as f:
            writer.write(f)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Resize error: {str(e)}")

    return return_file_response(output_path, return_type, "letter_size.pdf", input_path)

@app.post("/extract-text", tags=["Text & OCR"])
async def extract_text(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    ocr_images: bool = Form(True, description="Use OCR to extract text from images in PDF")
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
        from PyPDF2 import PdfReader
        import pytesseract
        from PIL import Image
        import pdf2image
        
        reader = PdfReader(input_path)
        extracted_text = []
        
        for page_num, page in enumerate(reader.pages, 1):
            page_text = ""
            
            # Try to extract text directly from PDF
            try:
                direct_text = page.extract_text()
                if direct_text.strip():
                    page_text = direct_text
            except:
                pass
            
            # If no text found and OCR is enabled, try OCR
            if not page_text.strip() and ocr_images:
                try:
                    # Convert PDF page to image
                    images = pdf2image.convert_from_path(input_path, first_page=page_num, last_page=page_num)
                    if images:
                        # OCR the image
                        ocr_text = pytesseract.image_to_string(images[0])
                        if ocr_text.strip():
                            page_text = ocr_text
                except Exception as ocr_error:
                    # OCR failed, but don't error out
                    page_text = f"[OCR failed for page {page_num}: {str(ocr_error)}]"
            
            extracted_text.append({
                "page": page_num,
                "text": page_text.strip() if page_text else "[No text found]"
            })
        
        # Cleanup
        if os.path.exists(input_path):
            os.remove(input_path)
        
        return {
            "total_pages": len(extracted_text),
            "pages": extracted_text,
            "full_text": "\n\n".join([f"=== Page {p['page']} ===\n{p['text']}" for p in extracted_text])
        }

    except Exception as e:
        # Cleanup on error
        if os.path.exists(input_path):
            os.remove(input_path)
        raise HTTPException(status_code=500, detail=f"Text extraction error: {str(e)}")

@app.delete("/delete", tags=["Cache Management"])
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

@app.delete("/clear-cache", tags=["Cache Management"])
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

@app.get("/cache/status", tags=["Cache Management"])
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

@app.post("/cleanup/run", tags=["Cache Management"])
async def manual_cleanup(request: Request):
    """Manually trigger the cleanup process"""
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)
    
    # Run cleanup
    deleted_count = cleanup_expired_files()
    
    # Get updated file count
    folder = Path(TEMP_DIR)
    remaining_files = len(list(folder.glob("*")))
    
    return {
        "message": "Manual cleanup completed",
        "deleted_files": deleted_count,
        "remaining_files": remaining_files,
        "cleanup_mode": CLEANUP_MODE,
        "file_expiration_minutes": FILE_EXPIRATION_MINUTES
    }

@app.get("/cleanup/status", tags=["Cache Management"])
def cleanup_status(request: Request):
    """Get cleanup configuration and status"""
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)
    
    folder = Path(TEMP_DIR)
    file_count = len(list(folder.glob("*")))
    
    status = {
        "cleanup_mode": CLEANUP_MODE,
        "file_expiration_minutes": FILE_EXPIRATION_MINUTES,
        "current_file_count": file_count
    }
    
    if CLEANUP_MODE == "active":
        global cleanup_task
        status["active_cleanup_running"] = cleanup_task is not None and not cleanup_task.done()
        status["cleanup_interval_minutes"] = CLEANUP_INTERVAL_MINUTES
    else:
        status["lazy_cleanup_info"] = "Cleanup runs on each API request"
    
    return status