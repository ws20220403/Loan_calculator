# -*- coding: utf-8 -*-
"""
주택 대출 계산기 — 데이터 갱신 스크립트 (표준 라이브러리만 사용)

사용법:
  py update_rates.py
      → index.html의 내장 JSON(#loan-data)을 loan_products.json으로 내보내기(동기화만, 기준일 유지)

  py update_rates.py --fetch [금감원_API_KEY]
      → ① 정책대출: 주택도시기금·주금공 공식 페이지의 금리표를 스크래핑해 갱신 (키 불필요)
        ② 시중은행: 금감원 finlife API로 전세대출·주담대 금리 갱신 (키 있을 때만)
        성공한 소스만 반영하고 dataDate를 오늘로 갱신. 실패 소스는 이전 값 유지 + 경고 기록.

finlife API 키 발급(무료): https://finlife.fss.or.kr → 오픈API → 인증키 신청
주의: finlife·기금 서버 모두 웹방화벽이 비브라우저 User-Agent를 차단하므로 브라우저 UA 필수.
"""
import json
import re
import sys
import datetime
import urllib.request
import urllib.error
from html.parser import HTMLParser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HTML_PATH = BASE_DIR / "index.html"
JSON_PATH = BASE_DIR / "loan_products.json"

BLOCK_RE = re.compile(
    r'(<script type="application/json" id="loan-data">\s*)(\{.*?\})(\s*</script>)',
    re.DOTALL,
)

FINLIFE_BASE = "https://finlife.fss.or.kr/finlifeapi"
TOP_FIN_GRP = "020000"  # 은행권
# 웹방화벽이 비브라우저 User-Agent 요청을 응답 없이 끊으므로 브라우저 UA 필수
UA_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}

# 자동수집 표식(notes 말미) — 갱신 시 제거 후 재부착
MARK_RE = re.compile(r"\s*·\s*(?:finlife \d+개 옵션|공식페이지 자동수집) 기준 \d{4}-\d{2}-\d{2}$")

# ---------------------------------------------------------------------------
# 정책대출 소스 정의
#   parse:
#     incomeTable-max  — 소득구간 행 × 보증금구간 열 표 → 행별 최고금리(보수적) [버팀목류]
#     incomeTable-last — 소득구간 행 × 만기 열 표 → 행별 마지막 열(최장만기)  [디딤돌류]
#     fixed-first      — 본문에서 첫 "연 X.X%" 값 (범위 검증)                [중기청]
#     fixed-minmax     — 페이지 내 유효범위 금리들의 min~max                  [보금자리론]
# ---------------------------------------------------------------------------
NHUF = "https://nhuf.molit.go.kr/FP/FP05"
POLICY_SOURCES = [
    {"id": "butimok-general",  "url": f"{NHUF}/FP0502/FP05020101.jsp", "titleKey": "버팀목전세자금",   "parse": "incomeTable-max"},
    {"id": "butimok-newlywed", "url": f"{NHUF}/FP0502/FP05020401.jsp", "titleKey": "신혼부부전용 전세", "parse": "incomeTable-max"},
    {"id": "butimok-youth",    "url": f"{NHUF}/FP0502/FP05020301.jsp", "titleKey": "청년전용 버팀목",   "parse": "incomeTable-max"},
    {"id": "newborn-jeonse",   "url": f"{NHUF}/FP0502/FP05021401.jsp", "titleKey": "신생아",           "parse": "incomeTable-max"},
    # 중소기업취업청년 대출은 단독 상품 종료(청년 버팀목 우대로 통합)되어 소스에서 제외 (2026-07 확인)
    {"id": "didimdol-general", "url": f"{NHUF}/FP0503/FP05030101.jsp", "titleKey": "디딤돌",           "parse": "incomeTable-last",
     "alsoApply": ["didimdol-first"]},  # 생애최초는 전용 페이지가 없어 일반 디딤돌 금리표 공유
    {"id": "didimdol-newlywed","url": f"{NHUF}/FP0503/FP05030601.jsp", "titleKey": "신혼부부전용 구입", "parse": "incomeTable-last"},
    {"id": "newborn-purchase", "url": f"{NHUF}/FP0503/FP05030801.jsp", "titleKey": "신생아",           "parse": "incomeTable-last"},
    {"id": "bogeumjari",       "url": "https://www.hf.go.kr/ko/sub01/sub01_01_04.do", "titleKey": "보금자리론", "parse": "fixed-minmax", "range": (2.0, 8.0)},
]


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------
def http_bytes(url, post_data=None, retries=2):
    """GET/POST 공통 요청. 일시적 타임아웃 재시도 + 사내망 SSL 검사 환경 인증서 검증 폴백."""
    import time
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=post_data, headers=UA_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.read()
        except urllib.error.URLError as e:
            # 사내망 SSL 검사 등으로 인증서 검증이 불가한 환경 폴백 (공개 금리 페이지 + 값 범위 검증으로 위험 완화)
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=30, context=ctx) as res:
                    return res.read()
            last_err = e            # 타임아웃 등 일시 오류 → 재시도
        except TimeoutError as e:
            last_err = e
        if attempt < retries:
            time.sleep(3)
    raise last_err


def fetch_html(url):
    raw = http_bytes(url)
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# 기금 페이지는 금리 셀이 비어 있고 AJAX(/init/include/NewBabyGetInform.jsp)가 td id로 채운다.
# → 같은 엔드포인트를 호출해 {tdId: 금리}를 받아 HTML에 주입(hydrate)한 뒤 표를 파싱한다.
AJAX_CALL_RE = re.compile(r'url\s*:\s*"([^"]*NewBabyGetInform[^"]*)"\s*,\s*data\s*:\s*(\{[^}]*\})', re.S)

def hydrate_nhuf(html):
    import urllib.parse
    merged = {}
    for url_path, data_js in AJAX_CALL_RE.findall(html):
        params = dict(re.findall(r'"(\w+)"\s*:\s*"([^"]*)"', data_js))
        endpoint = urllib.parse.urljoin("https://nhuf.molit.go.kr/", url_path)
        body = urllib.parse.urlencode(params).encode()
        text = http_bytes(endpoint, post_data=body).decode("utf-8", errors="replace")
        # 응답은 {arrJson : [{"LOANRATE":"2.5",...}]} 형태의 비표준 JSON — 숫자 값만 추출("null" 제외)
        for k, v in re.findall(r'"(\w+)"\s*:\s*"(\d+(?:\.\d+)?)"', text):
            merged[k] = v
    for k, v in merged.items():
        html = re.sub(rf'(<(td|th)[^>]*\bid="{k}"[^>]*>).*?(</\2>)', rf"\g<1>{v}%\g<3>", html, flags=re.S)
    return html, len(merged)


UNIT_MANWON = {"억": 10000, "천만": 1000, "백만": 100, "만": 1}

def parse_manwon_last(text):
    """텍스트에서 마지막 금액을 만원 단위로. '2천만원 초과 4천만원 이하' → 4000, '1.3억원' → 13000."""
    # '1억3천만' 같은 복합 표기를 하나의 금액으로 묶기 위해 연속 세그먼트를 그룹화
    tokens = re.findall(r"([\d,.]+)\s*(억|천만|백만|만)", text)
    if not tokens:
        return None
    amounts, cur = [], 0.0
    prev_mult = None
    for num, unit in tokens:
        try:
            val = float(num.replace(",", "")) * UNIT_MANWON[unit]
        except ValueError:
            continue
        mult = UNIT_MANWON[unit]
        # 이전 토큰보다 작은 단위가 이어지면 같은 금액의 연속 표기('1억' + '3천만')
        if prev_mult is not None and mult < prev_mult:
            cur += val
        else:
            if prev_mult is not None:
                amounts.append(cur)
            cur = val
        prev_mult = mult
    amounts.append(cur)
    return int(round(amounts[-1]))


def parse_rates(text):
    """'연 2.2%' 형태의 금리들(%p 제외)을 float 리스트로."""
    return [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%(?!p)", text)]


class TableParser(HTMLParser):
    """페이지의 모든 <table>을 [[셀텍스트,...], ...] 구조로 수집 (중첩 테이블 스택 처리)."""
    def __init__(self):
        super().__init__()
        self.tables = []      # 완성된 테이블 목록
        self._stack = []      # 진행 중 테이블 스택
        self._row = None
        self._cell = None
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "table" and self._stack:
            self.tables.append(self._stack.pop())
        elif tag == "tr" and self._stack and self._row is not None:
            if self._row:
                self._stack[-1].append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            if self._row is not None:
                self._row.append(text)
            self._cell = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._in_title:
            self.title += data


def extract_income_table(tables, pick):
    """소득구간 금리표 추출 → [{'incomeMax': 만원, 'rate': %}] (pick: 'max'|'last')."""
    best = None
    for tbl in tables:
        rows = []
        for row in tbl:
            if not row:
                continue
            head = row[0]
            # 행 머리가 소득구간('~이하')이고 나머지 셀에 금리가 있어야 유효 행
            if "이하" not in head:
                continue
            income = parse_manwon_last(head)
            rates = []
            for cell in row[1:]:
                rates.extend(parse_rates(cell))
            rates = [r for r in rates if 0.5 <= r <= 10]
            if income and rates:
                rate = max(rates) if pick == "max" else rates[-1]
                rows.append({"incomeMax": income, "rate": rate})
        # 유효 행 2개 이상 + 소득 오름차순이면 채택 (가장 행이 많은 표 우선)
        if len(rows) >= 2 and all(rows[i]["incomeMax"] < rows[i+1]["incomeMax"] for i in range(len(rows)-1)):
            if best is None or len(rows) > len(best):
                best = rows
    return best


# ---------------------------------------------------------------------------
# 갱신 로직
# ---------------------------------------------------------------------------
def load_embedded():
    html = HTML_PATH.read_text(encoding="utf-8")
    m = BLOCK_RE.search(html)
    if not m:
        sys.exit("오류: index.html에서 #loan-data JSON 블록을 찾지 못했습니다.")
    return html, m, json.loads(m.group(2))


def save_both(html, m, data):
    body = json.dumps(data, ensure_ascii=False, indent=2)
    new_html = html[: m.start(2)] + body + html[m.end(2):]
    HTML_PATH.write_text(new_html, encoding="utf-8")
    JSON_PATH.write_text(body, encoding="utf-8")
    print(f"저장 완료: {HTML_PATH.name} (내장 블록) / {JSON_PATH.name}")


def get_product(data, pid):
    for prod in data["products"]:
        if prod["id"] == pid:
            return prod
    return None


def mark_notes(prod, source_label, today):
    base = MARK_RE.sub("", prod.get("notes", ""))
    prod["notes"] = f"{base} · {source_label} 기준 {today}"


def update_policy(data, today, warnings):
    """정책대출 공식 페이지 스크래핑. 성공 상품 수를 반환."""
    ok_count = 0
    for src in POLICY_SOURCES:
        pid = src["id"]
        try:
            html = fetch_html(src["url"])
            if "NewBabyGetInform" in html:
                html, n_rates = hydrate_nhuf(html)   # AJAX 금리값을 표에 주입
            tp = TableParser()
            tp.feed(html)
            if src["titleKey"] not in tp.title and src["titleKey"] not in html[:4000]:
                raise ValueError(f"페이지 제목 불일치(예상 '{src['titleKey']}', 실제 '{tp.title.strip()[:40]}')")

            if src["parse"].startswith("incomeTable"):
                pick = "max" if src["parse"].endswith("max") else "last"
                table = extract_income_table(tp.tables, pick)
                if not table:
                    raise ValueError("금리표를 찾지 못함(페이지 구조 변경 가능성)")
                targets = [pid] + src.get("alsoApply", [])
                for t in targets:
                    prod = get_product(data, t)
                    if prod:
                        prod["rate"] = {"kind": "incomeTable", "table": table}
                        mark_notes(prod, "공식페이지 자동수집", today)
                print(f"  {pid}: {len(table)}개 소득구간 "
                      f"({table[0]['rate']}~{table[-1]['rate']}%)"
                      + (f" → {src['alsoApply']}에도 적용" if src.get("alsoApply") else ""))
            elif src["parse"] == "fixed-first":
                lo, hi = src["range"]
                rates = [r for r in parse_rates(html) if lo <= r <= hi]
                if not rates:
                    raise ValueError("본문에서 금리를 찾지 못함")
                prod = get_product(data, pid)
                prod["rate"] = {"kind": "fixed", "rateMin": rates[0], "rateMax": rates[0]}
                mark_notes(prod, "공식페이지 자동수집", today)
                print(f"  {pid}: 연 {rates[0]}%")
            elif src["parse"] == "fixed-minmax":
                # hf.go.kr 금리표는 '%' 없이 숫자만(예: 5.00) 표기 — '만기' 표의 숫자 셀을 직접 수집
                lo, hi = src["range"]
                rates = sorted(
                    float(cell)
                    for tbl in tp.tables if tbl and any("만기" in c for c in tbl[0])
                    for row in tbl[1:] for cell in row
                    if re.fullmatch(r"\d+(?:\.\d+)?", cell) and lo <= float(cell) <= hi
                )
                if len(rates) < 2:
                    raise ValueError("금리표를 찾지 못함")
                prod = get_product(data, pid)
                prod["rate"] = {"kind": "fixed", "rateMin": rates[0], "rateMax": rates[-1]}
                mark_notes(prod, "공식페이지 자동수집", today)
                print(f"  {pid}: 연 {rates[0]}~{rates[-1]}%")
            ok_count += 1
        except Exception as e:
            msg = f"{pid}: 자동수집 실패 — {e}"
            warnings.append(msg)
            print(f"  ⚠️ {msg}")
    return ok_count


def fetch_finlife(api_name, auth_key):
    """finlife API 한 종을 페이지 순회하며 (baseList, optionList)를 모아 반환."""
    bases, options = [], []
    page = 1
    while True:
        url = (f"{FINLIFE_BASE}/{api_name}.json"
               f"?auth={auth_key}&topFinGrpNo={TOP_FIN_GRP}&pageNo={page}")
        payload = json.loads(http_bytes(url).decode("utf-8"))
        result = payload.get("result", {})
        if result.get("err_cd") not in ("000", None):
            raise RuntimeError(f"finlife API 오류({api_name}): {result.get('err_cd')} {result.get('err_msg')}")
        bases.extend(result.get("baseList") or [])
        options.extend(result.get("optionList") or [])
        max_page = int(result.get("max_page_no") or 1)
        if page >= max_page:
            break
        page += 1
    return bases, options


def bank_details(bases, options, predicate=None):
    """은행별 상품 상세 목록: [{bank, name, rateMin, rateMax, limit}] (최저금리순)."""
    meta = {}
    for b in bases:
        meta[(b.get("fin_co_no"), b.get("fin_prdt_cd"))] = {
            "bank": b.get("kor_co_nm", ""), "name": b.get("fin_prdt_nm", ""),
            "limit": (b.get("loan_lmt") or "").strip(),
        }
    agg = {}
    for o in options:
        if predicate and not predicate(o):
            continue
        key = (o.get("fin_co_no"), o.get("fin_prdt_cd"))
        try:
            lo, hi = float(o.get("lend_rate_min")), float(o.get("lend_rate_max"))
        except (TypeError, ValueError):
            continue
        cur = agg.setdefault(key, [lo, hi])
        cur[0], cur[1] = min(cur[0], lo), max(cur[1], hi)
    out = []
    for key, (lo, hi) in agg.items():
        m = meta.get(key, {"bank": "", "name": "", "limit": ""})
        out.append({"bank": m["bank"], "name": m["name"], "rateMin": round(lo, 2), "rateMax": round(hi, 2), "limit": m["limit"]})
    out.sort(key=lambda x: (x["rateMin"], x["rateMax"]))
    return out


def rate_range(options, predicate=None):
    """옵션들에서 (최저금리, 최고금리, 상품 수)를 계산. predicate로 옵션 필터링."""
    mins, maxs = [], []
    for o in options:
        if predicate and not predicate(o):
            continue
        try:
            lo = float(o.get("lend_rate_min"))
            hi = float(o.get("lend_rate_max"))
        except (TypeError, ValueError):
            continue
        mins.append(lo)
        maxs.append(hi)
    if not mins:
        return None
    return round(min(mins), 2), round(max(maxs), 2), len(mins)


def set_fixed_rate(data, product_id, lo, hi, count, today):
    prod = get_product(data, product_id)
    if not prod:
        print(f"  경고: 상품 id '{product_id}'를 찾지 못해 건너뜀")
        return
    prod["rate"] = {"kind": "fixed", "rateMin": lo, "rateMax": hi}
    mark_notes(prod, f"finlife {count}개 옵션", today)
    print(f"  {product_id}: 연 {lo}~{hi}% ({count}개 옵션)")


def is_variable(option):
    """주담대 옵션이 변동금리인지 (lend_rate_type: C=고정, D=변동)."""
    t = (option.get("lend_rate_type") or "").upper()
    n = option.get("lend_rate_type_nm") or ""
    return t == "D" or "변동" in n


def update_banks(data, auth_key, today, warnings):
    """finlife API로 시중은행 금리 + 은행별 상세 목록(bankDetails) 갱신. 성공 상품 수를 반환."""
    ok_count = 0
    details = data.setdefault("bankDetails", {})
    try:
        rent_bases, rent_opts = fetch_finlife("rentHouseLoanProductsSearch", auth_key)
        r = rate_range(rent_opts)
        if r:
            set_fixed_rate(data, "bank-jeonse", *r, today)
            details["jeonse"] = bank_details(rent_bases, rent_opts)
            ok_count += 1
    except Exception as e:
        warnings.append(f"bank-jeonse: finlife 실패 — {e}")
        print(f"  ⚠️ bank-jeonse: {e}")
    try:
        mort_bases, mort_opts = fetch_finlife("mortgageLoanProductsSearch", auth_key)
        rv = rate_range(mort_opts, is_variable)
        if rv:
            set_fixed_rate(data, "bank-mortgage-var", *rv, today)
            details["mortgageVar"] = bank_details(mort_bases, mort_opts, is_variable)
            ok_count += 1
        rf = rate_range(mort_opts, lambda o: not is_variable(o))
        if rf:
            set_fixed_rate(data, "bank-mortgage-mixed", *rf, today)
            details["mortgageMixed"] = bank_details(mort_bases, mort_opts, lambda o: not is_variable(o))
            ok_count += 1
    except Exception as e:
        warnings.append(f"bank-mortgage: finlife 실패 — {e}")
        print(f"  ⚠️ bank-mortgage: {e}")
    return ok_count


def main():
    html, m, data = load_embedded()

    if len(sys.argv) >= 2 and sys.argv[1] == "--fetch":
        auth_key = sys.argv[2].strip() if len(sys.argv) >= 3 else ""
        today = datetime.date.today().isoformat()
        warnings = []
        total_ok = 0

        print("① 정책대출 공식 페이지 수집 중 (주택도시기금·주금공)...")
        total_ok += update_policy(data, today, warnings)

        if auth_key:
            print("② finlife API에서 시중은행 금리 조회 중...")
            total_ok += update_banks(data, auth_key, today, warnings)
        else:
            print("② finlife API 키 미지정 — 시중은행 금리는 건너뜀")

        data["scrapeWarnings"] = warnings   # 앱이 경고 배너로 표시
        if total_ok == 0:
            sys.exit("오류: 모든 소스 수집에 실패했습니다. 데이터를 변경하지 않습니다.")
        data["dataDate"] = today
        print(f"성공 {total_ok}건 / 경고 {len(warnings)}건 · dataDate → {today}")
    elif len(sys.argv) == 1:
        print("내보내기 모드: 내장 JSON을 loan_products.json으로 동기화합니다 (기준일 유지).")
    else:
        sys.exit(__doc__)

    save_both(html, m, data)


if __name__ == "__main__":
    main()
