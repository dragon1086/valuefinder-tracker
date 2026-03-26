# ValueFinder Tracker

밸류파인더(valuefinder.co.kr) 리포트 수익률 트래커.

크롤러가 신규 리포트를 감지하고, 작성일 기준 주가 대비 현재 수익률을 추적합니다.
결과는 Next.js 대시보드로 시각화됩니다.

## 구조

```
valuefinder-tracker/
├── tracker.py          # Python 크롤러 (메인 로직)
├── requirements.txt    # Python 의존성
├── data/
│   └── reports.json    # tracker.py가 자동 생성 (JSON export)
├── db/
│   └── valuefinder.sqlite
├── logs/
└── web/                # Next.js 프론트엔드
    ├── src/app/
    │   ├── page.tsx          # 메인 대시보드
    │   └── api/reports/route.ts  # API route
    ├── public/
    │   └── reports.json      # 빌드 시 data/reports.json에서 복사
    └── vercel.json
```

## 로컬 실행

### 크롤러

```bash
# 의존성 설치
pip install -r requirements.txt

# 실행 (크롤링 + 수익률 리포트 + JSON export)
python tracker.py

# 수익률 리포트만 (크롤링 생략)
python tracker.py --report-only
```

### 프론트엔드

```bash
cd web

# 개발 서버
npm run dev
# http://localhost:3000 에서 확인

# 프로덕션 빌드
npm run build
npm start
```

> **참고:** 빌드 전 `data/reports.json`이 존재해야 합니다.
> `prebuild` 스크립트가 자동으로 `../data/reports.json → public/reports.json`으로 복사합니다.

## Vercel 배포

1. GitHub에 레포 푸시
2. [vercel.com](https://vercel.com) → New Project → 해당 레포 선택
3. **Root Directory** 설정: `web` (중요!)
4. Framework Preset: Next.js (자동 감지)
5. Deploy

> Vercel은 `web/` 디렉토리를 루트로 빌드합니다.
> `data/reports.json`은 `prebuild` 스크립트로 `public/`에 복사되어 정적 파일로 서빙됩니다.

## 자동화

`tracker.py`는 매일 실행되어:
1. 신규 리포트 크롤링 → 텔레그램 알림
2. 추적 종목 수익률 계산 → 텔레그램 알림
3. `data/reports.json` 업데이트 → git push (자동)

Vercel은 GitHub push 감지 시 자동 재배포됩니다.
