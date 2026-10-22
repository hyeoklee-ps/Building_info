# 건축물대장 정보 — GitHub Pages + Cloudflare Worker

기존 Streamlit 앱은 그대로 유지됩니다. 새 설치형 웹앱은 `docs/`, API 프록시는 `worker/index.js`에 있습니다.

## 1. GitHub Pages 켜기 (최초 1회)
https://github.com/hyeoklee-ps/Building_info/settings/pages 에서:
- Source: **Deploy from a branch**
- Branch: **main**
- Folder: **/docs**
- Save

게시 예정 주소: https://hyeoklee-ps.github.io/Building_info/ (Pages를 켜기 전에는 접속 불가)

## 2. Cloudflare Worker 배포 (최초 1회)
https://dash.cloudflare.com/ 에 가입/로그인 → **Workers & Pages** → 새 Worker 생성 → 기본 샘플 코드를
`worker/index.js` 전체 내용으로 교체 → Deploy.

Worker의 **Settings → Variables and Secrets**에서 다음을 **Secret** 타입으로 추가:
- `KAKAO_REST_API_KEY`: 기존 카카오 REST API 키
- `BUILDING_API_KEY`: 기존 건축HUB 일반 인증키
- `JUSO_API_KEY`: 기존 도로명주소 API 키 (선택 사항, 백업 주소 검색 경로)
추가로 `ALLOWED_ORIGIN` 변수는 `https://hyeoklee-ps.github.io`로 설정 (기본값 동일).

**절대 GitHub 코드나 Pages 화면에 인증키를 넣지 마세요.**

## 3. 웹앱 연결
GitHub Pages 주소에 접속 → '최초 1회 서버 주소 설정' → Worker의 `https://....workers.dev` 주소 입력 → 저장.
저장된 것은 Worker의 URL뿐이며, API 키는 Cloudflare에 있습니다.

## 4. 휴대폰 설치
안드로이드 Chrome → GitHub Pages 주소 → ⋮ 메뉴 → **홈 화면에 추가** 또는 **앱 설치** (브라우저 버전에 따라 다름).
이것은 별도의 APK가 아니라 설치형 웹사이트(PWA 형태)입니다.
GitHub Pages 화면 자체는 Streamlit 휴면 복귀가 없습니다. 다만 외부 API 호출은 네트워크 상태와 API 서버에 따라 지연될 수 있습니다.

## 확인 사항
- 실제 API 인증키를 이용한 통합 테스트는 아직 하지 않았습니다.
- 공개 Worker는 요금/호출량 악용 위험이 있습니다. Cloudflare에서 요청 제한과 사용량 알림을 설정하고 필요시 Access 인증을 추가하세요.
- 카카오 장소 검색은 층수 전용 데이터를 제공하지 않습니다.
- 기존 Streamlit Secrets는 Cloudflare Worker로 자동 이전되지 않습니다.
