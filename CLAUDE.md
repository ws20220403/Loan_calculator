# CLAUDE.md for 주택 대출 계산기

## Project Overview
개인용 무료 주택 대출 계산기. 사용자 프로필(소득·가족·자산)과 매물 정보(매매/전세/월세,
가격, 지역, 면적)를 입력하면, 내장된 대출 상품 데이터에서 자격이 되는 상품을 필터링하고
상품별 예상 금리·대출 한도·월 주거비 총액·RIR을 계산해 비교표로 보여준다.
기획서: `..\주택 대출 계산기 기획서_v2.md`

## Technical Stack
- 단일 HTML 파일 `index.html` (무의존: 외부 라이브러리·빌드·서버 없음)
- 대출 상품 데이터 (이중화):
  - 내장 스냅샷: HTML 내부 `<script type="application/json" id="loan-data">` 블록 (file:// 폴백)
  - 외부 파일: `loan_products.json` — 앱이 접속할 때마다 fetch(no-store)로 새로 로드해 교체
  - 두 파일은 `update_rates.py`가 동기화한다. **JSON 블록을 수동 수정하면 반드시 `py update_rates.py` 실행**
- 금리 자동 갱신: `py update_rates.py --fetch <KEY>` (금감원 finlife API, 시중은행 상품만).
  배포 후에는 `.github/workflows/update-data.yml`이 매일 06:00 KST 자동 실행 (secret: FINLIFE_API_KEY)
- 사용자 프로필·검색 기록: localStorage (기기 로컬에만 저장, 저장소에는 절대 커밋하지 않음)
- 호스팅: GitHub Pages 예정 (공개 저장소 — 코드와 상품 데이터만 공개, 개인정보 미포함)

## How to Run
- 로컬: `index.html`을 브라우저에서 직접 연다 (file:// 동작 보장)
- 개발 검증: `.claude/launch.json`의 `loancalc` (127.0.0.1:8799)

## Coding Conventions
- 변수명은 풀어서 명확하게 (예: monthlyHousingCost, loanLimitByLtv)
- 복잡한 금융 로직(한도 계산, 상환 공식, 자격 필터)에는 근거와 함께 주석을 충분히
- 들여쓰기 2칸, 함수는 단일 책임으로 작게
- 모든 금액 내부 표현은 **만원 단위 정수**, 표시할 때만 억/만원 포맷

## Anti-patterns / Things to Avoid
- 대출 상품 데이터를 JS 로직 코드에 흩어 하드코딩하지 말 것 — 반드시 loan-data JSON 블록 한 곳에
- 상품 데이터에는 `dataDate`(기준일)가 필수이며, 화면에 항상 노출할 것
- 개인정보(프로필)를 URL 파라미터·저장소 파일에 절대 넣지 말 것 — localStorage만 사용
- 계산 결과를 단정적으로 표현하지 말 것 — 항상 "추정치, 실제는 금융기관 심사에 따름" 고지 유지
- 상환방식을 무시한 일괄 원리금균등 계산 금지 — 상품별 repaymentTypes를 따를 것

## Important Considerations
- **은행 상품의 금리 변동성 판별은 정적 필드 `variability`("변동"|"혼합")로 할 것.**
  `rate.kind`는 finlife 자동갱신이 "fixed"로 덮어쓰므로 판별 기준으로 쓰면 스트레스 DSR이 조용히 죽는다(실제 발생했던 버그).
- 전세대출은 대부분 만기일시상환(매달 이자만 납부)이다. 원리금균등 공식을 일괄 적용하면 안 된다.
- 한도는 min(상품 최대한도, 보증금×보증비율 또는 매매가×유효LTV, DSR/DTI 역산 한도, 규제 상한)로 계산한다.
- 유효 LTV = min(상품 LTV, 지역 규제 LTV). 규제지역(서울 전역·경기 일부) 40%, 그 외 70%. (2026-07 기준)
- 규제지역 주담대 절대 상한: 주택가 15억 이하 6억 / 15~25억 4억 / 25억 초과 2억. (2026-07 기준)
- 정책대출(버팀목·디딤돌·신생아특례)은 부부합산 소득·순자산·주택가격/면적 상한을 모두 확인한다.
- 응답은 전부 클라이언트 계산이므로 즉시 표시된다. 로딩 상태 불필요.

## Data Update Workflow
1. 각 기관 고시 확인 (주택도시기금 nhuf.molit.go.kr / 주금공 hf.go.kr / 금감원 finlife.fss.or.kr)
2. `index.html`의 `#loan-data` JSON 블록 수치 수정
3. `dataDate` 갱신 (6개월 초과 시 화면에 노후 경고가 자동 표시됨)
