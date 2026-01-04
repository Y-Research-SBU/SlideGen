# webui/backend/main.py
import os
import sys
import tempfile
import uuid
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
 
PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

app = FastAPI(title="SlideGen WebUI API")
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs: Dict[str, Dict[str, Any]] = {}
job_logs: Dict[str, List[str]] = {}
job_lock = threading.Lock()


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending | processing | completed | failed
    progress: int
    message: str
    error: Optional[str] = None


def _add_log(job_id: str, msg: str) -> None:
    with job_lock:
        job_logs.setdefault(job_id, [])
        job_logs[job_id].append(msg)


def _set_job(job_id: str, **kwargs) -> None:
    with job_lock:
        jobs.setdefault(job_id, {})
        jobs[job_id].update(kwargs)


def _safe_stem(name: str) -> str:
    # 简单做一下文件名清洗
    stem = Path(name).stem
    stem = stem.replace(" ", "_")
    return "".join(ch for ch in stem if ch.isalnum() or ch in ("_", "-"))[:80] or "paper"


def _get_available_models() -> List[str]:
    # 你可以按 SlideGen 支持的别名来列（utils/wei_utils.py 有 4o / 4o-mini / gpt-4.1 / gpt-5 等）
    return ["4o", "4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-5", "o1", "o3"]


def _run_slide_generation(
    job_id: str,
    pdf_bytes: bytes,
    original_filename: str,
    model_name_t: str,
    model_name_v: str,
    formula_mode: int,
    no_blank_detection: bool,
) -> None:
    try:
        _set_job(job_id, status="processing", progress=3, message="Preparing job workspace...")
        _add_log(job_id, f"[job] PROJECT_ROOT={PROJECT_ROOT}")

        # 为 job 建临时目录
        job_dir = Path(tempfile.mkdtemp(prefix=f"slidegen_{job_id}_"))
        pdf_path = job_dir / "paper.pdf"
        pdf_path.write_bytes(pdf_bytes)

        stem = _safe_stem(original_filename)
        paper_name = f"{stem}_{job_id[:8]}"  # 隔离输出，避免并发冲突

        _add_log(job_id, f"[job] paper_name={paper_name}")
        _set_job(job_id, progress=8, message="Launching SlideGen pipeline...")

        # 关键：API key 不要在前端传；放在后端环境变量
        # OpenAI 官方建议用环境变量管理 key，避免浏览器端暴露。
        env = os.environ.copy()

        # 如果你使用“中转网关”，建议在启动后端前在 shell 中设置环境变量，例如：
        #   export OPENAI_API_KEY="sk-..."
        #   export OPENAI_BASE_URL="https://api.example.com/v1"
        # 注意：不要把 key 写进前端或提交到仓库。

        # OpenAI Python SDK 支持创建 client 时传 base_url（社区示例）。
        # 注意：你的 SlideGen 依赖的上层库（例如 CAMEL / openai SDK）具体读哪个变量，
        # 取决于你代码如何初始化 client；最稳妥是你在代码里显式传 base_url。
        #
        # 这里不强制写死，只读取你已 export 的环境变量。

        cmd = [
            sys.executable, "-m", "SlidesAgent.new_pipeline_logtime",
            "--paper_path", str(pdf_path),
            "--paper_name", paper_name,
            "--model_name_t", model_name_t,
            "--model_name_v", model_name_v,
            "--tmp_dir", str(job_dir / "tmp"),
            "--formula_mode", str(formula_mode),
        ]
        if no_blank_detection:
            cmd.append("--no_blank_detection")

        _add_log(job_id, "[cmd] " + " ".join(cmd))
        _set_job(job_id, progress=12, message="Running SlideGen...")

        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )

        # 简单阶段进度（按日志关键词推进）
        progress = 12
        for line in proc.stdout:
            line = line.rstrip("\n")
            _add_log(job_id, line)

            # 你可以按自己 pipeline 的 log 关键词继续细化
            if "parse" in line.lower():
                progress = max(progress, 25)
            if "figure" in line.lower() or "filter" in line.lower():
                progress = max(progress, 45)
            if "layout" in line.lower() or "arranger" in line.lower():
                progress = max(progress, 70)
            if "generate_pptx_from_plan" in line.lower() or "save" in line.lower():
                progress = max(progress, 85)

            _set_job(job_id, progress=progress, message="Running SlideGen...")

        ret = proc.wait()
        if ret != 0:
            raise RuntimeError(f"SlideGen exited with code {ret}")

        _set_job(job_id, progress=92, message="Collecting outputs...")

        pptx_path = PROJECT_ROOT / "contents" / paper_name / f"{model_name_t}_{model_name_v}_output_slides_themed.pptx"
        if not pptx_path.exists():
            raise FileNotFoundError(f"Expected output pptx not found: {pptx_path}")

        _set_job(
            job_id,
            status="completed",
            progress=100,
            message="Completed",
            output_file=str(pptx_path),
            paper_name=paper_name,
        )
        _add_log(job_id, f"[done] pptx={pptx_path}")

    except Exception as e:
        _set_job(job_id, status="failed", progress=100, message="Failed", error=str(e))
        _add_log(job_id, f"[error] {e}")


@app.get("/")
async def root():
    return {"message": "SlideGen WebUI API"}


@app.get("/models")
async def get_models():
    return {"models": _get_available_models()}


@app.post("/generate", response_model=JobStatus)
async def generate_slides(
    background_tasks: BackgroundTasks,
    model_name_t: str = Form("4o"),
    model_name_v: str = Form("4o"),
    formula_mode: int = Form(1),
    no_blank_detection: bool = Form(False),
    pdf_file: UploadFile = File(...),
):
    models = _get_available_models()
    if model_name_t not in models or model_name_v not in models:
        raise HTTPException(status_code=400, detail="Unknown model name")

    job_id = uuid.uuid4().hex
    _set_job(job_id, status="pending", progress=0, message="Queued...")
    job_logs[job_id] = []

    pdf_bytes = await pdf_file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Empty PDF")

    background_tasks.add_task(
        _run_slide_generation,
        job_id,
        pdf_bytes,
        pdf_file.filename or "paper.pdf",
        model_name_t,
        model_name_v,
        int(formula_mode),
        bool(no_blank_detection),
    )

    return JobStatus(job_id=job_id, status="pending", progress=0, message="Job started")


@app.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    j = jobs[job_id]
    return JobStatus(
        job_id=job_id,
        status=j["status"],
        progress=j["progress"],
        message=j["message"],
        error=j.get("error"),
    )


@app.get("/logs/{job_id}")
async def get_logs(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"logs": job_logs.get(job_id, [])}


@app.get("/download/{job_id}")
async def download(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    j = jobs[job_id]
    if j["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job not completed")

    out = j.get("output_file")
    if not out or not Path(out).exists():
        raise HTTPException(status_code=404, detail="Output not found")

    paper_name = j.get("paper_name", "slides")
    filename = f"{paper_name}.pptx"
    return FileResponse(
        path=out,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=filename,
    )
