# 이커머스 사이트 분석기

## 프로젝트 개요

이커머스 사이트 URL을 입력하면 호스팅 솔루션, 기업 정보, 카카오톡 채널, 비즈니스 전략을 자동 분석하여 한국어 보고서를 생성하는 도구입니다.

**기술 스택:** Python · FastAPI · httpx · BeautifulSoup · OpenAI API (gpt-4o-mini)

---

## 실행 방법

### 사전 요구사항

- **Python 3.10+** (가상환경 사용 권장)

### 1. 저장소/프로젝트 이동

```bash
cd <프로젝트_폴더>
```

### 2. 가상환경 생성 및 활성화 (권장)

```bash
# 가상환경 생성
python -m venv .venv

# 활성화 (macOS/Linux)
source .venv/bin/activate

# 활성화 (Windows)
# .venv\Scripts\activate
```

### 3. 환경 변수 설정

```bash
cp .env.example .env
# .env 파일에 OPENAI_API_KEY 입력
# 발급: https://platform.openai.com/api-keys
```

### 4. 의존성 설치

```bash
pip install -r requirements.txt
```

### 5. 서버 실행

```bash
python run.py
```

또는 uvicorn으로 직접 실행 (개발 시 핫 리로드):

```bash
uvicorn app.main:app --reload --port 8000
```

### 6. 접속 및 확인

- 브라우저에서 **http://localhost:8000** 접속
- URL 입력 후 분석 실행

**실행 확인:** 터미널에 `Uvicorn running on http://0.0.0.0:8000` 또는 `Application startup complete` 메시지가 보이면 정상 기동된 것입니다.

---

## Vercel 배포

이 프로젝트는 **Vercel**에 배포할 수 있습니다. FastAPI가 단일 서버리스 함수로 동작하며, AI 보고서 생성(30초~1분)을 위해 `maxDuration`을 300초로 설정해 두었습니다.

### 배포 전 준비

1. **환경 변수**: Vercel 대시보드 → 프로젝트 → Settings → Environment Variables 에서 `OPENAI_API_KEY` 추가
2. **의존성**: `requirements.txt` 기준으로 자동 설치됨

### 배포 방법

**방법 1 – Git 연동 (권장)**

1. [Vercel](https://vercel.com) 로그인 후 **Add New Project**
2. GitHub/GitLab 등 저장소 연결 후 이 프로젝트 선택
3. Root Directory가 프로젝트 루트인지 확인
4. Environment Variables에 `OPENAI_API_KEY` 설정 후 Deploy

**방법 2 – Vercel CLI**

```bash
# CLI 설치 (최초 1회)
npm i -g vercel

# 프로젝트 루트에서 배포
cd <프로젝트_폴더>
vercel

# 환경 변수는 대시보드에서 설정하거나
vercel env add OPENAI_API_KEY
```

### 배포 시 주의사항

| 항목 | 설명 |
|------|------|
| **엔트리포인트** | `pyproject.toml`의 `[project.scripts]` 에서 `app = "app.main:app"` 로 FastAPI 앱 위치 지정 |
| **실행 시간** | `/analyze` 는 AI 생성으로 30초~1분 소요 가능. `vercel.json` 에서 `maxDuration: 300` 적용 |
| **응답 크기** | 최종 보고서 HTML이 4.5MB를 넘지 않도록 유지 (Vercel 함수 응답 제한) |
| **플랜** | Hobby 플랜에서도 기본 300초 제한으로 동작. Pro는 최대 800초까지 설정 가능 |

---

## 분석 기능

### 1. 호스팅 솔루션 식별

HTML 소스 코드를 교차 검증하여 이커머스 플랫폼을 탐지합니다.

| 플랫폼 | 감지 방법 |
|--------|----------|
| 카페24 | JS 변수 `CAFE24SHOP`, CDN `cafe24.com` |
| 메이크샵 | JS 변수 `MS_Shop_Config`, CDN `makeshop.co.kr` |
| 아임웹 | JS 변수 `imweb`, CDN `imweb.me` |
| 고도몰 | JS 변수 `gd_config`, CDN `godomall.com` |
| 위사 | JS 변수 `wisa`, CDN `wisacdn.com` |
| Shopify | JS 변수 `Shopify`, CDN `shopifycdn.com` |
| WooCommerce | JS 변수 `woocommerce`, 경로 `wp-content` |
| 그누보드/영카트 | JS 변수 `yna_cart`, `gnuboard` |

### 2. 기업 정보 추출

Footer 영역에서 정규식으로 자동 파싱합니다.

- 회사명 (주식회사, ㈜, (주) 패턴)
- 대표자
- 사업자등록번호
- 사업장 주소
- 전화번호 / 이메일

### 3. 카카오톡 채널 분석

1. HTML에서 `pf.kakao.com/{채널ID}` URL 추출
2. 해당 채널 페이지 직접 접속
3. `span.txt_friends` 요소에서 실시간 친구(팔로워) 수 파싱
4. 채널명 및 설명 추출

### 4. 카테고리 / 상품 분석

- 네비게이션 메뉴(`nav`, `gnb`, `lnb`)에서 카테고리 목록 추출
- 상품 이미지 alt 텍스트 기반 상품명 수집

### 5. AI 보고서 생성 (GPT-4o mini)

수집된 모든 데이터를 GPT-4o mini에 전달하여 한국어 전문 보고서를 생성합니다.

**보고서 구성:**
1. 호스팅 솔루션 식별 (기술적 근거 포함)
2. 기업 정보
3. 카카오톡 채널 분석
4. 주요 제품 및 카테고리 (`[이미지 삽입: 제품명]` 태그 포함)
5. 비즈니스 전략 및 특징 분석
6. 기술 스택 및 마케팅 도구

---

## 파일 구조

```
<프로젝트_폴더>/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI 앱 (라우팅)
│   ├── analyzer.py      # 분석 로직 + API 호출
│   └── templates/
│       └── index.html   # 웹 UI (SSE 실시간 진행상황)
├── log/                 # 로그 저장 (analysis.log, 로테이션 백업)
├── requirements.txt
├── .env.example
├── run.py               # 서버 실행 진입점
└── claude.md            # 이 파일
```

---

## API 엔드포인트

### 웹 UI

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/` | 메인 입력 화면 |
| `POST` | `/analyze` | SSE 스트리밍 분석 (웹 UI용) |

### REST API (추후 확장)

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/api/v1/analyze` | JSON 응답 (form-data: `url`) |

**REST API 예시:**
```bash
curl -X POST http://localhost:8000/api/v1/analyze \
  -F "url=https://www.example-shop.co.kr"
```

---

## SSE 이벤트 프로토콜

웹 UI는 Server-Sent Events로 실시간 진행 상황을 수신합니다.

```json
// 진행 이벤트
{"type": "progress", "step": 1, "message": "사이트에 접속 중..."}

// 결과 이벤트
{"type": "result", "report_html": "...", "raw": {...}}

// 오류 이벤트
{"type": "error", "message": "사이트 접속 실패: ..."}
```

**진행 단계:**

| Step | 내용 |
|------|------|
| 1 | 사이트 접속 및 HTML 수집 |
| 2 | 호스팅 솔루션 분석 |
| 3 | 기업 정보 · 카테고리 추출 |
| 4 | 카카오톡 채널 분석 |
| 5 | AI 보고서 생성 (30초~1분) |

---

## 주요 의존성

| 패키지 | 용도 |
|--------|------|
| `fastapi` | 웹 프레임워크 |
| `uvicorn` | ASGI 서버 |
| `httpx` | 비동기 HTTP 요청 |
| `beautifulsoup4` | HTML 파싱 |
| `openai` | OpenAI API 클라이언트 |
| `markdown` | 보고서 마크다운 → HTML 변환 |
| `python-dotenv` | 환경 변수 로드 |

---

## 참고 사항

- **로그**: 분석·에러 로그는 프로젝트 루트의 `log/analysis.log`에 쌓입니다. 최대 5MB·백업 3개 로테이션, `.gitignore`에 `log/` 포함.
- SSL 인증서 오류가 있는 사이트도 분석 가능 (`verify=False`)
- EUC-KR 인코딩 사이트 자동 처리
- JavaScript로 렌더링되는 콘텐츠는 분석 제한 있음
- 카카오톡 친구 수는 JS 렌더링 여부에 따라 추출 불가 가능
