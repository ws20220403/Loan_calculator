# 주택 대출 계산기

매매·전세·월세 각 시나리오에서 내 조건으로 가능한 대출 상품, 예상 금리, 월 주거비 총액,
RIR(월 소득 대비 주거비 비율)을 계산하는 개인용 무료 계산기. 단일 HTML, 외부 의존성 없음.

## 사용

- `index.html`을 브라우저에서 열기 (file:// 그대로 동작, 오프라인 가능)
- 또는 GitHub Pages URL 접속 (배포 시)

개인정보(프로필)는 기기 localStorage에만 저장되며 어디로도 전송되지 않는다.

## 데이터 구조 — "사용할 때마다 최신"이 동작하는 방식

1. 앱은 내장 스냅샷(`index.html` 안 `#loan-data`)으로 즉시 뜬 뒤,
   `loan_products.json`을 **캐시 없이(fetch no-store)** 다시 불러와 교체한다.
2. `loan_products.json`은 `update_rates.py`가 갱신한다:
   - `py update_rates.py` — 내장 JSON → loan_products.json 동기화 (수동 수정 후 실행)
   - `py update_rates.py --fetch <금감원_API_KEY>` — finlife API로 시중은행
     전세대출·주담대 금리를 실제 시장값으로 갱신 + dataDate 갱신
3. GitHub Pages 배포 후에는 `.github/workflows/update-data.yml`이 **매일 06:00(KST)**
   자동으로 2번을 실행해 커밋한다 → 접속할 때마다 그날 갱신된 데이터를 받는다.
   - 저장소 Settings → Secrets and variables → Actions에 `FINLIFE_API_KEY` 등록 필요
   - 키 발급(무료): https://finlife.fss.or.kr → 오픈API 인증키 신청

⚠️ 정책대출(버팀목·디딤돌·신생아특례·보금자리론) 금리는 공식 API가 없어 자동화 불가.
주택도시기금(nhuf.molit.go.kr)·주금공(hf.go.kr) 고시 확인 후 `index.html`의
`#loan-data` 블록을 수동 수정하고 `py update_rates.py`로 동기화한다.

## 개발

- 로컬 미리보기: `python -m http.server 8799 --directory .` 후 http://localhost:8799
- 프로젝트 규칙: `CLAUDE.md` / 기획서: `..\주택 대출 계산기 기획서_v2.md`
