# Bank Ontology Layer

AI 에이전트를 위한 은행 데이터 온톨로지 레이어 — BOAZ 27기 미니 프로젝트
팀원: 염지인, 이수민, 임지영

LLM 에이전트가 DB에 직접 SQL을 쓰지 않고, 온톨로지 레이어가 제공하는 도구로만 데이터를 조회·수정하도록 만드는 프로젝트임. 비즈니스 용어·코드값·조인 경로는 `ontology/ontology.yaml`에 한 번만 정의하고, 역할별 마스킹과 액션 승인은 서비스 계층에서 처리함.

```
app (Streamlit) → agent (LLM 도구 호출) → service (Ontology Service, FastAPI) → db (PostgreSQL)
```

자세한 설계는 [`docs/design.md`](docs/design.md) 참고.

---

## 실행 방법

사전 준비: [Docker Desktop](https://www.docker.com/products/docker-desktop/) 설치 후 실행

Windows (PowerShell)

```powershell
git clone https://github.com/<계정>/bank-ontology-layer.git
cd bank-ontology-layer
copy .env.example .env
docker compose up --build
```

macOS / Linux

```bash
git clone https://github.com/<계정>/bank-ontology-layer.git
cd bank-ontology-layer
cp .env.example .env
docker compose up --build
```

| 접속 | 주소 |
| --- | --- |
| 데모 화면 | http://localhost:8502 |
| API 문서 (Swagger) | http://localhost:8001/docs |

| 작업 | 명령 |
| --- | --- |
| 종료 | `docker compose down` |
| DB 데이터까지 초기화 | `docker compose down -v` |
| 서비스 테스트 | `docker compose run --rm service pytest` |

LLM을 사용하는 기능은 `.env`의 `LLM_API_KEY`를 채워야 동작함. `.env`는 Git에 올라가지 않음.

---

## 폴더 구조

| 경로 | 내용 | 담당 |
| --- | --- | --- |
| `ontology/ontology.yaml` | 객체·속성·링크·지표·액션·역할 정의 | 구조: 지인 / 내용: 지영 |
| `data/` | DB 스키마, 목데이터 생성·적재(seed) | 지영 |
| `service/` | Ontology Service (FastAPI) | 지인 |
| `agent/` | LLM 도구 정의, 에이전트 루프, 실행 기록 | 수민 |
| `app/` | Streamlit 데모 화면 | 수민 |
| `eval/` | 평가 질문셋, A/B/C 비교 평가 | 지영 |
| `docs/` | 설계서, API 계약, 응답 예시, 작업 목록 | 공통 |

---

## 팀

| 이름 | 파트 |
| --- | --- |
| 지인 | 시스템 (온톨로지 구조, Ontology Service, Docker, 통합) |
| 지영 | 데이터·온톨로지 정의·평가 |
| 수민 | 에이전트·데모·시연 |

---

## 협업 규칙

### 브랜치

| 브랜치 | 용도 |
| --- | --- |
| `main` | 항상 `docker compose up`으로 실행 가능한 상태 유지, 직접 push 금지 |
| `feat/<파트>-<기능>` | 기능 개발 후 PR로 병합 (예: `feat/data-seed`, `feat/agent-tools`) |

작업 순서

```bash
git checkout main
git pull
git checkout -b feat/data-seed
# 작업 후
git add .
git commit -m "feat: 목데이터 seed 적재 추가"
git push -u origin feat/data-seed
# GitHub에서 PR 생성 → 리뷰 후 병합
```

### 커밋 메시지

| 타입 | 용도 |
| --- | --- |
| feat | 새 기능 |
| fix | 버그 수정 |
| refactor | 동작 유지, 구조 개선 |
| style | 줄 맞춤·공백 등 동작과 무관한 수정 |
| docs | README 등 문서 |
| test | 테스트 추가·수정 |
| chore | 빌드 설정, 라이브러리 추가, 폴더 정리 |
