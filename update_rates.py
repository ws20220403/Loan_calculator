# -*- coding: utf-8 -*-
"""
주택 대출 계산기 — 데이터 갱신 스크립트 (표준 라이브러리만 사용)

사용법:
  py update_rates.py
      → index.html의 내장 JSON(#loan-data)을 loan_products.json으로 내보내기(동기화만, 기준일 유지)

  py update_rates.py --fetch <금감원_API_KEY>
      → 금감원 금융상품통합비교공시(finlife) API로 시중은행 전세대출·주담대 금리를 실제 시장값으로
        갱신하고, dataDate를 오늘로 바꾼 뒤 index.html과 loan_products.json 둘 다 갱신

API 키 발급(무료): https://finlife.fss.or.kr → 오픈API → 인증키 신청
정책대출(버팀목·디딤돌 등) 금리는 API가 없으므로 index.html의 JSON 블록을 직접 수정한 뒤
이 스크립트를 인자 없이 실행해 loan_products.json과 동기화한다.
"""
import json
import re
import sys
import datetime
import urllib.request
import urllib.error
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
# finlife 웹방화벽이 비브라우저 User-Agent 요청을 응답 없이 끊으므로 브라우저 UA 필수
UA_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}


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


def fetch_finlife(api_name, auth_key):
    """finlife API 한 종을 페이지 순회하며 optionList(금리 옵션)를 모아 반환."""
    options = []
    page = 1
    while True:
        url = (f"{FINLIFE_BASE}/{api_name}.json"
               f"?auth={auth_key}&topFinGrpNo={TOP_FIN_GRP}&pageNo={page}")
        req = urllib.request.Request(url, headers=UA_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as res:
            payload = json.loads(res.read().decode("utf-8"))
        result = payload.get("result", {})
        if result.get("err_cd") not in ("000", None):
            sys.exit(f"finlife API 오류({api_name}): {result.get('err_cd')} {result.get('err_msg')}")
        options.extend(result.get("optionList") or [])
        max_page = int(result.get("max_page_no") or 1)
        if page >= max_page:
            break
        page += 1
    return options


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
    for prod in data["products"]:
        if prod["id"] == product_id:
            prod["rate"] = {"kind": "fixed", "rateMin": lo, "rateMax": hi}
            # notes 말미의 이전 갱신 표식은 제거 후 새로 부착
            base_note = re.sub(r"\s*·\s*finlife \d+개 옵션 기준 \d{4}-\d{2}-\d{2}$", "", prod.get("notes", ""))
            prod["notes"] = f"{base_note} · finlife {count}개 옵션 기준 {today}"
            print(f"  {product_id}: 연 {lo}~{hi}% ({count}개 옵션)")
            return
    print(f"  경고: 상품 id '{product_id}'를 찾지 못해 건너뜀")


def is_variable(option):
    """주담대 옵션이 변동금리인지 (lend_rate_type: C=고정, D=변동)."""
    t = (option.get("lend_rate_type") or "").upper()
    n = option.get("lend_rate_type_nm") or ""
    return t == "D" or "변동" in n


def main():
    html, m, data = load_embedded()

    if len(sys.argv) >= 3 and sys.argv[1] == "--fetch":
        auth_key = sys.argv[2].strip()
        today = datetime.date.today().isoformat()
        print("finlife API에서 시중은행 금리 조회 중...")

        # 전세자금대출
        rent_opts = fetch_finlife("rentHouseLoanProductsSearch", auth_key)
        r = rate_range(rent_opts)
        if r:
            set_fixed_rate(data, "bank-jeonse", *r, today)

        # 주택담보대출 (변동 / 고정·혼합 구분)
        mort_opts = fetch_finlife("mortgageLoanProductsSearch", auth_key)
        rv = rate_range(mort_opts, is_variable)
        if rv:
            set_fixed_rate(data, "bank-mortgage-var", *rv, today)
        rf = rate_range(mort_opts, lambda o: not is_variable(o))
        if rf:
            set_fixed_rate(data, "bank-mortgage-mixed", *rf, today)

        data["dataDate"] = today
        print(f"dataDate → {today}")
    elif len(sys.argv) == 1:
        print("내보내기 모드: 내장 JSON을 loan_products.json으로 동기화합니다 (기준일 유지).")
    else:
        sys.exit(__doc__)

    save_both(html, m, data)


if __name__ == "__main__":
    main()
