"""
이커머스 사이트 분석 모듈

사이트 URL을 받아 호스팅 솔루션, 기업 정보,
상품/카테고리를 분석하고 OpenAI API로 보고서를 생성합니다.
"""

import re
import json
from typing import AsyncGenerator

import asyncio
import os
import logging
import logging.handlers

import httpx
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
import markdown as md_lib

# ── 로거 설정 ─────────────────────────────────────────────────────────────────

logger = logging.getLogger("site_analyzer")
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    # Vercel 등 서버리스: 파일 시스템 쓰기 불가 → 콘솔만 사용
    _log_dir = os.path.join(os.path.dirname(__file__), "..", "log")
    _log_file = os.path.join(_log_dir, "analysis.log")
    try:
        os.makedirs(_log_dir, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            _log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(fh)
    except OSError:
        # 읽기 전용(서버리스) 등: 파일 핸들러 생략, 스트림만
        pass

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(ch)

# ── 브라우저 헤더 ──────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
}

# ── 호스팅 솔루션 감지 패턴 ───────────────────────────────────────────────────

HOSTING_INDICATORS: dict[str, dict[str, list[str]]] = {
    "카페24": {
        "js_vars": ["CAFE24SHOP", "cafe24"],
        "cdn_domains": ["cafe24.com", "cafe24shop", "cafe24cdn"],
    },
    "메이크샵": {
        "js_vars": ["MS_Shop_Config", "makeshop"],
        "cdn_domains": ["makeshop.co.kr", "makeshopcorp"],
    },
    "아임웹": {
        "js_vars": ["imweb", "imwebShop"],
        "cdn_domains": ["imweb.me", "imwebcdn"],
    },
    "고도몰": {
        "js_vars": ["gd_config", "godomall"],
        "cdn_domains": ["godomall.com", "godo.co.kr"],
    },
    "위사": {
        "js_vars": ["wisa", "wisaShop"],
        "cdn_domains": ["wisacdn.com", "wisa.co.kr"],
    },
    "쇼피파이(Shopify)": {
        "js_vars": ["Shopify", "shopify"],
        "cdn_domains": ["shopify.com", "shopifycdn.com", "myshopify.com"],
    },
    "워드프레스/WooCommerce": {
        "js_vars": ["woocommerce", "wp_ajax"],
        "cdn_domains": ["wp-content/plugins/woocommerce", "wp-content/themes"],
    },
    "그누보드/영카트": {
        "js_vars": ["yna_cart", "gnuboard"],
        "cdn_domains": ["ycart", "gnuboard"],
    },
}


# ── 페이지 패치 ────────────────────────────────────────────────────────────────

async def fetch_page(url: str, timeout: int = 20) -> tuple[str, str]:
    """URL을 GET 요청으로 가져오고 (html, final_url) 반환"""
    logger.debug(f"[fetch_page] 요청: {url} (timeout={timeout}s)")
    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=timeout,
        verify=False,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()

        # 한국 사이트의 EUC-KR 인코딩 처리
        encoding_used = resp.encoding or "utf-8"
        if resp.encoding and resp.encoding.lower() in ("iso-8859-1", "latin-1"):
            try:
                html = resp.content.decode("euc-kr")
                encoding_used = "euc-kr (강제 디코딩)"
            except (UnicodeDecodeError, LookupError):
                html = resp.text
        else:
            html = resp.text

        logger.info(f"[fetch_page] 완료: {resp.url} | 상태={resp.status_code} | 인코딩={encoding_used} | HTML길이={len(html)}")
        return html, str(resp.url)


# ── 호스팅 솔루션 감지 ────────────────────────────────────────────────────────

def detect_hosting(html: str) -> dict[str, list[str]]:
    """HTML 소스에서 호스팅 솔루션 식별"""
    html_lower = html.lower()
    detected: dict[str, list[str]] = {}

    for hosting, indicators in HOSTING_INDICATORS.items():
        evidence: list[str] = []

        for var in indicators["js_vars"]:
            if var.lower() in html_lower:
                evidence.append(f"JS 코드/변수 '{var}' 감지")

        for domain in indicators["cdn_domains"]:
            if domain.lower() in html_lower:
                evidence.append(f"CDN/리소스 경로 '{domain}' 감지")

        if evidence:
            detected[hosting] = evidence

    if detected:
        logger.info(f"[detect_hosting] 감지된 호스팅: {list(detected.keys())}")
        for h, ev in detected.items():
            logger.debug(f"  - {h}: {ev}")
    else:
        logger.info("[detect_hosting] 호스팅 솔루션 미감지")

    return detected


# ── 푸터 기업 정보 추출 ───────────────────────────────────────────────────────

def extract_footer_info(soup: BeautifulSoup) -> dict:
    """Footer 영역에서 회사 정보 추출"""
    footer = (
        soup.find("footer")
        or soup.find(id=re.compile(r"footer|foot", re.I))
        or soup.find(class_=re.compile(r"footer|foot", re.I))
        or soup.find("div", id=re.compile(r"bot|bottom", re.I))
    )

    if not footer:
        # 마지막 수단: body 하단 div
        all_divs = soup.find_all("div")
        footer = all_divs[-1] if all_divs else None

    footer_text = footer.get_text(" ", strip=True) if footer else ""
    info: dict = {"raw_text": footer_text[:3000]}

    # 사업자등록번호
    biz = re.search(r"사업자\s*등록\s*번호\s*:?\s*(\d{3}-\d{2}-\d{5})", footer_text)
    if biz:
        info["사업자번호"] = biz.group(1)

    # 대표자
    rep = re.search(r"대표(?:자|이사)?\s*[:\s]+([가-힣]{2,6})", footer_text)
    if rep:
        info["대표자"] = rep.group(1).strip()

    # 회사명 (주식회사 패턴)
    corp = re.search(r"(?:주식회사|㈜|\(주\))\s*([\w가-힣]+)", footer_text)
    if not corp:
        corp = re.search(r"([\w가-힣]+\s*(?:주식회사|㈜|\(주\)))", footer_text)
    if corp:
        info["회사명"] = corp.group(0).strip()

    # 전화번호
    phone = re.search(r"(?:전화|TEL|Tel|T)\s*[.:\s]+([0-9\-()]{8,16})", footer_text)
    if phone:
        info["전화번호"] = phone.group(1).strip()

    # 이메일
    email = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", footer_text)
    if email:
        info["이메일"] = email.group(0)

    # 주소 (도로명 또는 지번)
    addr = re.search(
        r"(?:주소|소재지|Address)\s*[:\s]+([^\n,]{10,60}(?:시|군|구|동|로|길)[^\n,]{0,40})",
        footer_text,
    )
    if addr:
        info["주소"] = addr.group(1).strip()

    return info


# ── 카테고리 / 상품 추출 ──────────────────────────────────────────────────────

def extract_nav_categories(soup: BeautifulSoup) -> list[str]:
    """네비게이션 메뉴에서 카테고리 추출"""
    nav = (
        soup.find("nav")
        or soup.find(id=re.compile(r"nav|gnb|lnb|menu|category", re.I))
        or soup.find(class_=re.compile(r"gnb|lnb|nav.*menu|main.*nav|main.*menu", re.I))
    )

    categories: list[str] = []
    if nav:
        for a in nav.find_all("a", limit=40):
            text = a.get_text(strip=True)
            if text and 1 < len(text) < 25 and text not in categories:
                categories.append(text)

    return categories


def extract_product_images(soup: BeautifulSoup) -> list[dict]:
    """대표 상품 이미지 추출 (alt 텍스트 포함)"""
    images: list[dict] = []
    for img in soup.find_all("img", limit=50):
        alt = img.get("alt", "").strip()
        src = img.get("src", "").strip()
        if not src or src.startswith("data:"):
            continue
        if alt and len(alt) > 1:
            images.append({"alt": alt, "src": src})
        if len(images) >= 15:
            break
    return images


# ── 대분류/소분류 카테고리 분류 ─────────────────────────────────────────────────

MA_MAJOR: dict[str, str] = {
    "00": "대분류",
    "01": "인터넷,컴퓨터",
    "02": "쇼핑(종합관)",
    "03": "비즈니스,경제",
    "04": "뉴스,미디어",
    "05": "금융,부동산",
    "06": "엔터테인먼트",
    "07": "정보통신업",
    "08": "쇼핑(패션,생활관)",
    "09": "서비스",
    "10": "온라인교육",
    "11": "교육,학원",
    "12": "정치,행정,법",
    "13": "제조업",
    "14": "여행",
    "15": "생활,가정,취미",
    "16": "사회,문화,종교",
    "17": "레저,취미",
    "18": "문학,예술",
    "19": "건강,의학",
    "20": "미지정",
    "21": "기타",
}

# 중분류: {대분류코드: {중분류코드: 이름}}
MA_MINOR: dict[str, dict[str, str]] = {
    "01": {"AA": "포털", "BB": "프로그래밍정보", "CC": "웹디자인,그래픽", "DD": "웹서비스", "EE": "기타", "FF": "게임"},
    "02": {
        "01": "종합쇼핑몰", "02": "도서/공연/문화", "03": "컴퓨터,주변기기",
        "04": "해외쇼핑대행", "05": "가전(생활/계절/소형)", "06": "영상,음향",
        "07": "휴대폰,디카,PMP", "08": "사무용품", "09": "꽃배달",
        "10": "판촉물,기념품,행사용품", "11": "정수기(자판기)판매,대여", "12": "기타쇼핑몰",
    },
    "03": {
        "13": "PC방", "14": "프랜차이즈", "15": "대기업,계열사",
        "16": "출판", "17": "건설,건축", "18": "브랜드",
        "19": "프랜차이즈", "20": "기타", "21": "유통,판매",
    },
    "04": {
        "22": "종합,지역", "23": "일간지", "24": "인터넷",
        "25": "뉴스,", "26": "신문", "27": "위성,케이블채널", "28": "웹진",
    },
    "05": {
        "29": "금융(은행/증권/카드)", "30": "부동산종합정보", "31": "금융(투자)",
        "32": "포털", "33": "자동차보험", "34": "생명보험", "35": "대출",
        "36": "신용정보", "37": "보험정보",
    },
    "06": {
        "38": "음악감상", "39": "엔터테인먼트", "40": "포털", "41": "휴대폰",
        "42": "벨소리,SMS", "43": "운세", "44": "성인정보", "45": "영화홍보", "46": "기타",
    },
    "07": {
        "47": "웹호스팅", "48": "웹에이전시", "49": "바이러스", "50": "백신",
        "51": "개발", "52": "임대형쇼핑몰", "53": "솔루션", "54": "ERP,그룹웨어",
        "55": "개발", "56": "인터넷서비스업체", "57": "시스템통합(SI)",
        "58": "초고속인터넷가입센터", "59": "통신,네트워크프로그램",
        "60": "개발,공급", "61": "기타", "62": "소프트웨어", "63": "개발",
    },
    "08": {
        "64": "중고차", "65": "브랜드의류,명품중고", "66": "선물,디자인소품",
        "67": "화장품", "68": "신발,운동화", "69": "보세의류",
        "70": "자동차,카네비게이션", "71": "유아용품,완구", "72": "스포츠용품,운동기구",
        "73": "식품,농수산물", "74": "애완동물용품", "75": "건강식품,제품",
        "76": "생활용품", "77": "인테리어용품,수예", "78": "가구", "79": "아동복",
        "80": "귀금속,악세사리", "81": "의료기기", "82": "패션잡화",
        "83": "헤어,미용", "84": "낚시용품", "85": "성인용품",
        "86": "언더웨어", "87": "공구/금고", "88": "의,약품",
    },
    "09": {
        "89": "사진,이미지,영상서비스", "90": "온라인", "91": "사진인화",
        "92": "요식업", "93": "인쇄,명함", "94": "국제전화,선불카드",
        "95": "통역,번역서비스", "96": "자동차정비", "97": "사진관,스튜디오",
        "98": "피부미용,헤어", "99": "청소,세탁", "A0": "기타서비스",
        "A1": "구인,구직", "A2": "마케팅,", "A3": "광고대행",
        "A4": "세무,회계", "A5": "창업,컨설팅", "A6": "이사",
        "A7": "운송", "A8": "도우미,용역", "A9": "렌탈,대여",
    },
    "10": {"B0": "직무과정", "B1": "유아,초,중,고", "B2": "고시,", "B3": "공무원", "B4": "외국어", "B5": "온라인IT교육", "B6": "기타"},
    "11": {
        "B7": "유학,어학연수", "B8": "대학교", "B9": "자격증학원",
        "C1": "외국어학원", "C2": "컴퓨터,IT학원", "C3": "입시,편입학원",
        "C4": "운전면허학원", "C5": "기타교육기관", "C6": "직업,",
        "C7": "전문학원", "C8": "부설기관,", "C9": "단체",
        "D1": "고시,공무원",
    },
    "12": {"D2": "정부,공공기관", "D3": "법률", "D4": "기타"},
    "13": {
        "D5": "가전,디지털제품", "D6": "자동차부품", "D7": "섬유,패션제조",
        "D8": "기계,설비", "D9": "화학제조", "E1": "가구제조",
        "E2": "산업용품제조", "E3": "가정용품제조", "E4": "건축,인테리어자재제조",
        "E5": "종이,펄프제조",
    },
    "14": {
        "E6": "여행사", "E7": "콘도,리조트,호텔", "E8": "항공사,항공권예약",
        "E9": "종합여행정보", "F1": "펜션", "F2": "숙박예약",
        "F3": "제주여행정보,여행사", "F4": "해외여행정보", "F5": "렌터카", "F6": "여권,비자발급대행",
    },
    "15": {"F7": "출산,육아정보", "F8": "결혼정보회사,미팅,채팅", "F9": "생활정보", "G1": "애완견", "G2": "포털", "G3": "웨딩정보"},
    "16": {"G4": "종교", "G5": "사회봉사,시설", "G6": "기타"},
    "17": {"G7": "스포츠,레저", "G8": "취미"},
    "18": {"G9": "문학,예술"},
    "19": {
        "H1": "병원", "H2": "성형외과", "H3": "피부미용/다이어트",
        "H4": "건강,의학", "H5": "포털", "H6": "정보", "H7": "비뇨기과",
        "H8": "안과", "H9": "건강,의학관련단체", "I1": "치과병원",
        "I2": "피부과", "I3": "한의원,한방병원", "I4": "모발이식,탈모관리",
        "I5": "산부인과",
    },
    "20": {"I6": "미지정"},
    "21": {"I7": "기타"},
}


def _build_category_tier_prompt() -> str:
    """GPT에 전달할 대분류/소분류 카테고리 목록 문자열 생성"""
    lines = ["[대분류]"]
    for code, name in MA_MAJOR.items():
        lines.append(f"  {code}: {name}")
    lines.append("\n[중분류] (대분류코드, 중분류코드, 이름)")
    for major_code, minors in MA_MINOR.items():
        for minor_code, minor_name in minors.items():
            lines.append(f"  {major_code},{minor_code},{minor_name}")
    return "\n".join(lines)


_CATEGORY_TIER_PROMPT_CACHE = _build_category_tier_prompt()


async def classify_category_tier(collected: dict) -> dict:
    """수집된 사이트 데이터를 대분류/소분류 카테고리로 분류"""
    client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    site_info = (
        f"URL: {collected['url']}\n"
        f"타이틀: {collected.get('title', '')}\n"
        f"메타설명: {collected.get('meta_description', '')}\n"
        f"카테고리메뉴: {', '.join(collected.get('categories', [])[:15])}\n"
        f"상품: {', '.join(img['alt'] for img in collected.get('product_images', [])[:8])}\n"
        f"본문: {collected.get('body_text', '')[:400]}"
    )

    prompt = f"""아래 이커머스 사이트 정보를 보고 대분류/소분류 카테고리를 분류하세요.

{site_info}

대분류/소분류 카테고리 목록:
{_CATEGORY_TIER_PROMPT_CACHE}

반드시 JSON만 응답하세요:
{{"major_code": "코드2자리", "major_name": "대분류명", "minor_code": "코드", "minor_name": "중분류명"}}"""

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=120,
            temperature=0,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        logger.info(f"[classify_category_tier] 분류 결과: {result}")
        return result
    except Exception as e:
        logger.error(f"[classify_category_tier] 분류 실패: {e}")
        return {"major_code": "20", "major_name": "미지정", "minor_code": "I6", "minor_name": "미지정"}


# ── OpenAI API 보고서 생성 ───────────────────────────────────────────────────

async def generate_report(collected: dict) -> str:
    """수집된 데이터를 OpenAI API로 분석하여 마크다운 보고서 생성"""
    client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    # ── 토큰 절약: 핵심 데이터만 추출 ──────────────────────────────────────────
    hosting_text = (
        "; ".join(
            f"{k}: {', '.join(v)}" for k, v in collected["hosting"].items()
        )
        if collected["hosting"]
        else "감지 없음"
    )

    footer_data = {k: v for k, v in collected["footer"].items() if k != "raw_text"}
    footer_text = ", ".join(f"{k}: {v}" for k, v in footer_data.items())
    footer_raw = collected["footer"].get("raw_text", "")[:600]

    categories_text = ", ".join(collected.get("categories", [])[:15]) or "감지 불가"

    images = collected.get("product_images", [])
    images_text = ", ".join(img["alt"] for img in images[:5]) if images else "감지 불가"

    body_sample = collected.get("body_text", "")[:800]

    category_tier = collected.get("category_tier", {})
    category_tier_text = (
        f"{category_tier.get('major_code', '-')} {category_tier.get('major_name', '-')} > "
        f"{category_tier.get('minor_code', '-')} {category_tier.get('minor_name', '-')}"
        if category_tier else "미분류"
    )

    user_prompt = f"""이커머스 사이트를 분석하고 한국어 보고서를 작성하세요.

URL: {collected['url']}
타이틀: {collected.get('title', '')}
메타설명: {collected.get('meta_description', '')}
호스팅감지: {hosting_text}
기업정보(footer): {footer_text}
footer원문: {footer_raw}
대분류/소분류 카테고리: {category_tier_text}
카테고리: {categories_text}
상품이미지alt: {images_text}
본문샘플: {body_sample}

아래 6개 섹션으로 보고서 작성 (확인="✅", 추정="📌"):

# 이커머스 사이트 분석 보고서
## 1. 호스팅 솔루션 식별
**호스팅사:** [명칭] (근거: [변수/경로])
## 2. 기업 정보
회사명, 대표자, 사업자번호, 주소, 연락처
## 3. 대분류/소분류 카테고리
**대분류:** [코드] [명칭] / **중분류:** [코드] [명칭]
## 4. 주요 제품 및 카테고리
제품군 분류, `[이미지 삽입: 제품명]` 태그 포함
## 5. 비즈니스 전략 및 특징 분석
타겟 고객층, 마케팅 전략
## 6. 기술 스택 및 마케팅 도구
GA, 픽셀 등 감지 도구"""

    system_prompt = (
        "당신은 이커머스 기술 스택 분석가이자 비즈니스 전략가입니다. "
        "제공된 사이트 분석 데이터를 바탕으로 정확하고 전문적인 한국어 보고서를 작성합니다. "
        "확인된 사실과 추정/분석을 명확히 구분하고, 기술적 증거를 구체적으로 명시하세요."
    )

    # 429 Rate Limit 자동 재시도 (최대 3회)
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=4096,
                temperature=0.4,
            )
            return response.choices[0].message.content

        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "RateLimitError" in type(e).__name__

            if is_rate_limit and attempt < max_retries - 1:
                wait_sec = 15 * (attempt + 1)
                await asyncio.sleep(wait_sec)
                continue

            if is_rate_limit:
                raise RuntimeError(
                    "OpenAI API 요청 한도 초과 (429). "
                    "잠시 후 다시 시도하거나, 사용량 및 크레딧을 확인하세요."
                ) from e
            raise


# ── 메인 분석 파이프라인 (AsyncGenerator) ────────────────────────────────────

async def run_analysis(url: str) -> AsyncGenerator[dict, None]:
    """
    사이트 분석 파이프라인. 진행 이벤트를 yield하고 최종 결과를 반환.
    각 yield: {"type": "progress"|"result"|"error", ...}
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    logger.info(f"{'='*60}")
    logger.info(f"[run_analysis] 분석 시작: {url}")
    logger.info(f"{'='*60}")

    collected: dict = {}

    # ── Step 1: 페이지 접속 ──────────────────────────────────────────────────
    yield {"type": "progress", "step": 1, "message": "사이트에 접속 중..."}
    logger.info("[Step 1] 메인 페이지 접속")

    try:
        html, final_url = await fetch_page(url)
        soup = BeautifulSoup(html, "html.parser")

        title_el = soup.find("title")
        meta_desc_el = soup.find("meta", attrs={"name": "description"})

        collected["url"] = final_url
        collected["title"] = title_el.get_text(strip=True) if title_el else ""
        collected["meta_description"] = (
            meta_desc_el.get("content", "") if meta_desc_el else ""
        )
        collected["body_text"] = soup.get_text(" ", strip=True)

        logger.info(f"[Step 1] 완료 | 제목='{collected['title']}' | 최종URL={final_url}")

    except httpx.HTTPStatusError as e:
        logger.error(f"[Step 1] HTTP 오류 {e.response.status_code}: {url}")
        yield {"type": "error", "message": f"사이트 HTTP 오류 ({e.response.status_code}): {url}"}
        return
    except httpx.RequestError as e:
        logger.error(f"[Step 1] 접속 실패: {e}")
        yield {"type": "error", "message": f"사이트 접속 실패: {str(e)}"}
        return
    except Exception as e:
        logger.error(f"[Step 1] 예상치 못한 오류: {e}")
        yield {"type": "error", "message": f"예상치 못한 오류: {str(e)}"}
        return

    # ── Step 2: 호스팅 솔루션 감지 ──────────────────────────────────────────
    yield {"type": "progress", "step": 2, "message": "호스팅 솔루션 분석 중..."}
    logger.info("[Step 2] 호스팅 솔루션 감지")
    collected["hosting"] = detect_hosting(html)

    # ── Step 3: 기업 정보 및 카테고리 추출 ──────────────────────────────────
    yield {"type": "progress", "step": 3, "message": "기업 정보 및 카테고리 추출 중..."}
    logger.info("[Step 3] 기업 정보 및 카테고리 추출")
    collected["footer"] = extract_footer_info(soup)
    collected["categories"] = extract_nav_categories(soup)
    collected["product_images"] = extract_product_images(soup)

    footer_summary = {k: v for k, v in collected["footer"].items() if k != "raw_text"}
    logger.info(f"[Step 3] 기업정보: {footer_summary}")
    logger.info(f"[Step 3] 카테고리({len(collected['categories'])}개): {collected['categories'][:10]}")
    logger.info(f"[Step 3] 상품이미지({len(collected['product_images'])}개)")

    # ── Step 4: 대분류/소분류 카테고리 분류 ─────────────────────────────────────
    yield {"type": "progress", "step": 4, "message": "대분류/소분류 카테고리 분류 중..."}
    logger.info("[Step 4] 대분류/소분류 카테고리 분류 시작")
    collected["category_tier"] = await classify_category_tier(collected)
    logger.info(f"[Step 4] 대분류/소분류 카테고리: {collected['category_tier']}")

    # ── Step 5: AI 보고서 생성 ───────────────────────────────────────────────
    yield {"type": "progress", "step": 5, "message": "AI 보고서 생성 중... (30초~1분 소요)"}
    logger.info("[Step 5] OpenAI 보고서 생성 시작")

    try:
        report_md = await generate_report(collected)
        report_html = md_lib.markdown(
            report_md,
            extensions=["extra", "sane_lists"],
            output_format="html5",
        )
        logger.info(f"[Step 5] 보고서 생성 완료 (마크다운 {len(report_md)}자)")
    except Exception as e:
        logger.error(f"[Step 5] 보고서 생성 실패: {e}")
        yield {"type": "error", "message": f"보고서 생성 실패: {str(e)}"}
        return

    logger.info(f"[run_analysis] 분석 완료: {collected['url']}")
    logger.info(f"{'='*60}")

    # ── 최종 결과 반환 ────────────────────────────────────────────────────────
    yield {
        "type": "result",
        "report_html": report_html,
        "raw": {
            "url": collected["url"],
            "title": collected["title"],
            "hosting": collected["hosting"],
            "footer": {k: v for k, v in collected["footer"].items() if k != "raw_text"},
            "category_tier": collected.get("category_tier", {}),
            "categories": collected.get("categories", []),
        },
    }
