"""
이커머스 사이트 분석기 - FastAPI 웹 앱
"""

import json
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .analyzer import run_analysis

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="이커머스 사이트 분석기", version="1.0.0")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def sse_event(data: dict) -> str:
    """Server-Sent Event 형식으로 직렬화"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/analyze")
async def analyze(url: str = Form(...)):
    """사이트 분석 SSE 스트림 엔드포인트"""

    async def event_stream():
        async for event in run_analysis(url.strip()):
            yield sse_event(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 추후 REST API 엔드포인트 (v1) ────────────────────────────────────────────

@app.post("/api/v1/analyze")
async def api_analyze(url: str = Form(...)):
    """
    REST API 엔드포인트 (추후 확장용)
    SSE 대신 최종 결과만 JSON으로 반환
    """
    result = {}
    async for event in run_analysis(url.strip()):
        if event["type"] == "result":
            result = event
        elif event["type"] == "error":
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=event["message"])

    return result
