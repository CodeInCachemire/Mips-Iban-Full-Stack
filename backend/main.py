import json
import os
import re
import subprocess
from slowapi import Limiter
from slowapi.util import get_ipaddr
from slowapi.middleware import SlowAPIMiddleware
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from contextlib import asynccontextmanager
from backend import db
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
mips_timeout = int(os.getenv("MIPS_TIMEOUT", "10"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield
app = FastAPI(lifespan=lifespan)
port = os.getenv("PORT", "8000")
#CORS should exist before any and all routes
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    f"http://localhost:{port},http://127.0.0.1:{port}"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)

limiter = Limiter(key_func=get_ipaddr)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(req: Request, exception: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail" : "Rate limit exceeded please try again."},
    )

app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/")
def serve_frontend():
    # Serve the frontend HTML
    return FileResponse("frontend/index.html")

@app.get("/history")
@limiter.limit("30/minute")
def read_history(request: Request):
    rows = db.read_conversion()
    data = []
    for row in rows:
        entry = {
            "id" : row[0],
            "direction" : row[1],
            "input" : json.loads(row[2]),
            "output" : json.loads(row[3]),
            "created_at": row[4],
        }
        data.append(entry)
    return data

def mask_number(s:str, mask_char="*"):
    return s[:2] + mask_char * (len(s) - 5) + s[-3:]
def mask_iban(s:str, mask_char="*"):
    return s[:6] + mask_char * (len(s) - 9) + s[-3:]

@app.get("/")
def serve_frontend():
    # Correct relative path inside Docker (/app/static/index.html)
    return FileResponse("frontend/index.html")

class ConversionRequest(BaseModel):
    mode: str = Field(
        ...,
        description="Operation mode: 'IBAN' or 'KNRBLZ'",
        json_schema_extra={"example": "IBAN"},
        min_length=4,
        max_length=6,
    )
    value1: str = Field(
        ...,
        description="IBAN or KNR depending on mode",
        json_schema_extra={"example": "DE02120300000000202051"},
    )
    value2: str | None = Field(
        None,
        description="BLZ value (required only for KNRBLZ mode)",
        json_schema_extra={"example": "12030000"},
    )
    
    @field_validator('mode')
    @classmethod
    def validate_mode(cls, mode):
        if mode.upper() not in ['IBAN', 'KNRBLZ']:
            raise ValueError('Mode must be IBAN or KNRBLZ')
        return mode.upper()
    
    @field_validator('value1')
    @classmethod
    def validate_value1(cls, v1, info):
        mode = info.data.get('mode', '').upper()
        if mode == 'IBAN':
            if not re.match(r'^[A-Za-z]{2}\d{20}$', v1):
                raise ValueError('IBAN must be 22 characters: 2 letters followed by 20 digits.')
        elif mode == 'KNRBLZ':
            if not re.match(r'^\d{10}$', v1):
                raise ValueError('KNR must be exactly 10 digits.')
        return v1
    
    @field_validator('value2')
    @classmethod
    def validate_value2(cls, v, info):
        mode = info.data.get('mode', '').upper()
        
        if mode == 'KNRBLZ':
            if not v:
                raise ValueError('BLZ (value2) is required for KNRBLZ mode.')
            if not re.match(r'^\d{8}$', v):
                raise ValueError('BLZ must be exactly 8 digits.')
        return v
                
            
@app.post("/run")
@limiter.limit("15/second;40/minute")
def run(request: Request, req: ConversionRequest):
    mode = req.mode.upper()
    input_dict = {}
    logger.info(f"Processing {mode} conversion request")

    if mode == "IBAN":
        input_text = f"IBAN\n{req.value1.upper()}\n"
        masked_input = mask_iban(req.value1.upper())
        input_dict = {"IBAN":masked_input}

    elif mode == "KNRBLZ":
        if req.value2 is None:
            raise HTTPException(
                status_code=400,
                detail="value2 (BLZ) required for KNRBLZ mode"
            )
        input_text = f"KNRBLZ\n{req.value1}\n{req.value2}\n"
        masked_KNR = mask_number(req.value1)
        masked_BLZ = mask_number(req.value2)
        input_dict = {"KNR":masked_KNR, "BLZ" :masked_BLZ}

    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid mode. Use 'IBAN' or 'KNRBLZ'."
        )

    cmd = [
        "java", "-jar", "/app/mars.jar",
        "ae127",          # assembler error exit code
        "se126",          # simulator error exit code
        "me",             # terminate on runtime error
        "nc",             # no copyright
        "sm", "50000000", # max instruction steps
        "/app/src/iban2knr.asm",
        "/app/src/knr2iban.asm",
        "/app/src/moduloStr.asm",
        "/app/src/verify_iban.asm",
        "/app/src/util.asm",
        "/app/src/validateChecksum.asm",
        "/app/src/main.asm",
    ]

    try:
        result = subprocess.run(
            cmd,
            input=input_text,     
            text=True,
            capture_output=True,
            timeout=mips_timeout,
            check=True,
        )
        logger.info(f"MIPS execution completed successfully for {mode}")
    except subprocess.TimeoutExpired:
        logger.error(f"MIPS execution timed out for {mode} conversion")
        raise HTTPException(
            status_code=500,
            detail="MIPS execution timed out"
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"MIPS execution failed for {mode}: {e.stderr}")
        raise HTTPException(
            status_code=500,
            detail=e.stderr or "MIPS execution failed"
        )
    
    lines = result.stdout.splitlines()

    if not lines:
        raise HTTPException(
            status_code=500,
            detail="No output from MIPS program execution"
        )

    if lines[0] == "ERR":
        message = "Unknown error"
        for line in lines:
            if line.startswith("MSG="):
                message = line.split("=", 1)[1]
        raise HTTPException(status_code=400, detail=message)

    result = {}
    output_masked = {}
    status_msg = "Processing completed"  # Initialize with default value
    for i in lines:
        if i.startswith("MSG="):
            status_msg = i.split("=", 1)[1]
        elif "=" in i:
            k , v = i.split("=",1)
            if ((k == "BLZ" or k == "KNR") and mode == "IBAN") or (k == "IBAN" and mode == "KNRBLZ"):
                result[k] = v
                output_masked[k] = mask_number(v)
    
    if mode == "KNRBLZ":
        status_msg = "Successful KNR and BLZ parsed and valid IBAN generated!"
    
    try:
        db.log_conversion(mode, json.dumps(input_dict), json.dumps(output_masked))
        logger.info(f"Conversion logged to database: {mode}")
    except Exception as e:
        logger.error(f"Failed to log conversion to database: {e}")
    
    response = {"status_msg" : status_msg, "result" : result}
    return response

