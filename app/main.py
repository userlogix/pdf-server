from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uuid, os, shutil, base64
from datetime import datetime, timedelta
from app.utils import compress_pdf, download_pdf, generate_temp_url, validate_api_key

app = FastAPI()

TEMP_DIR = "/tmp/pdfcache"
os.makedirs(TEMP_DIR, exist_ok=True)

app.mount("/temp", StaticFiles(directory=TEMP_DIR), name="temp")

@app.post("/compress")
async def compress(
    request: Request,
    file: UploadFile = File(None),
    file_url: str = Form(None),
    return_type: str = Form("base64")  # base64, binary, or url
):
    api_key = request.headers.get("x-api-key")
    validate_api_key(api_key)

    if not file and not file_url:
        raise HTTPException(status_code=400, detail="Send file or file_url")

    input_path = os.path.join(TEMP_DIR, f"in_{uuid.uuid4()}.pdf")
    output_path = input_path.replace("in_", "out_")

    if file:
        with open(input_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    else:
        download_pdf(file_url, input_path)

    compress_pdf(input_path, output_path)

    if return_type == "binary":
        return FileResponse(output_path, media_type="application/pdf", filename="compressed.pdf")

    elif return_type == "url":
        url, expires = generate_temp_url(output_path)
        return {"url": url, "expires_at": expires.isoformat()}

    elif return_type == "base64":
        with open(output_path, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        return {
            "filename": "compressed.pdf",
            "content_type": "application/pdf",
            "content_base64": content
        }

    raise HTTPException(status_code=400, detail="Invalid return_type")