---
document_id: KIP-PRD-003
title: KIP v3 Agent-First Knowledge Fabric 제품 요구사항 정의서
version: 3.1.0
status: accepted
last_updated: 2026-08-13
language: ko-KR
audience:
  - product
  - engineering
  - ai-agent
  - knowledge-operations
source_of_truth: true
current_implementation: docs/IMPLEMENTATION_STATUS.md
supersedes:
  - KIP v2 Agent-First PRD
normative_keywords:
  MUST: 필수
  SHOULD: 강한 권고
  MAY: 선택
---

# KIP v3 Agent-First Knowledge Fabric PRD

## 0. 문서 사용법

이 문서는 NAS 문서, HWP/HWPX, PDF, XLSX, Slack, Apple Mail을 하나의 근거 체계로 연결하고 Claude Code, Codex 및 기타 AI agent가 안전하게 검색·열람·인용하도록 만드는 제품의 기준 문서다.

이 문서는 승인된 **제품 목표**다. 현재 구현 여부와 측정된 한계는
`docs/IMPLEMENTATION_STATUS.md`와 `docs/PRODUCTION_DESIGN_ALIGNMENT.md`가
판정한다. 이 문서의 현재형 문구만으로 기능이 구현·승격·운영 승인됐다고
해석하지 않는다.

- `MUST`는 출시 전 반드시 충족해야 한다.
- `SHOULD`는 특별한 사유가 없으면 충족해야 한다.
- `MAY`는 요구가 확인된 뒤 구현할 수 있다.
- 요구사항 식별자는 변경하지 않는다. 문구가 바뀌더라도 ID는 유지한다.
- 구현 세부사항은 `docs/TRD.md`를 따른다.

### 0.1 AI reading map

| Need | Read first |
|---|---|
| 최종 stack 결정 | §1, §12 |
| 사용자·제품 목표 | §2-§8 |
| 구현해야 할 기능 | §9 |
| 품질·보안 기준 | §10-§11 |
| 출시 순서 | §14 |
| 인수 조건 | §16 |
| 고정된 결정과 용어 | §17-§18 |
| 근거 자료 | §19 |

---

## 1. 최종 제품 결정

### 1.1 권장 기준 구성

KIP v3의 기준 구성은 다음과 같다.

```text
원본 소스
  ├─ 회사 NAS: HWP/HWPX/PDF/XLSX/DOCX/PPTX/CSV/MD
  ├─ Slack: 채널, DM, 스레드, 파일
  └─ Apple Mail 또는 IMAP: 메시지, 스레드, 첨부파일
          │
          ▼
Source Connector + Parser Broker
          │
          ▼
PostgreSQL 18
  ├─ 원본 식별자·리비전·ACL
  ├─ 논리 문서·콘텐츠 단위·근거 locator
  ├─ 엔티티·온톨로지·검증된 assertion
  ├─ 전문 검색 projection
  ├─ 관계형 graph projection
  └─ pgvector semantic projection(선택 활성)
          │
          ▼
Shell CLI + versioned JSON
          │
          ▼
AGENTS.md + CLAUDE.md + Skills
          │
          ▼
Claude Code / Codex / 기타 AI agent
```

### 1.2 데이터베이스 선택

| 선택지 | 최종 역할 | 결정 |
|---|---|---|
| SQLite 단일 DB | 소형·오프라인·개인용 경량 배포의 미래 옵션 | 기준 구현에서 제외 |
| PostgreSQL | canonical state, 동시 수집, ACL, 감사, 검색 projection의 기준 저장소 | **채택** |
| pgvector | 의미 검색용 재생성 가능한 projection | **PostgreSQL 프로덕션 참조 profile에 필수; 의미 검색은 기본 비활성** |
| Neo4j | 깊은 경로 탐색·그래프 알고리즘이 입증된 뒤 붙이는 read projection | **MVP 제외, 도입 게이트 통과 시 전용 포트와 함께 도입** |
| Apache AGE | PostgreSQL 내부 graph adapter 후보 | 기본 제외 |

PostgreSQL을 채택하는 이유는 단순 파일 색인을 넘어 Slack·메일·복수 worker·리비전·삭제·권한·감사·관계 검토를 함께 처리해야 하기 때문이다. SQLite는 5천 개 파일 검색만 놓고 보면 충분하지만, KIP v3의 전체 범위에서는 운영 제약이 빠르게 커진다.

Neo4j는 온톨로지 원장으로 사용하지 않는다. 그래프가 중요한 제품 기능으로 검증되기 전까지 PostgreSQL의 assertion 테이블과 재귀 질의로 처리한다. 향후 Neo4j를 도입하더라도 PostgreSQL에서 재생성 가능한 projection으로만 운영한다.

#### 1.2.1 Decision matrix

| Criterion | SQLite + FTS5 | PostgreSQL + pg_trgm/FTS | PostgreSQL + pgvector | Neo4j |
|---|---|---|---|---|
| 단일 사용자·5천 파일 | 매우 적합 | 적합하나 운영비 증가 | 불필요할 수 있음 | 과함 |
| NAS·Slack·Mail 동시 수집 | 제한적 | **적합** | PostgreSQL과 동일 | 별도 ingestion 원장 필요 |
| 리비전·검토 transaction | 가능하나 worker 조정 필요 | **강함** | PostgreSQL과 동일 | 관계에는 강하나 전체 원장에는 부적합 |
| source ACL·RLS | application 구현 부담 | **내장 RLS 활용** | PostgreSQL과 동일 | 별도 ACL 설계·projection 필요 |
| 정확 문자열·한국어 검색 | FTS5 + custom tokenizer | **pre-tokenized FTS + pg_trgm** | 대체하지 않음 | 주력 기능 아님 |
| 의미 유사 검색 | 별도 vector engine | 별도 extension 필요 | **적합, 선택 활성** | vector index가 있어도 canonical search와 분리 필요 |
| 1-4 hop 승인 관계 | recursive CTE 가능 | **충분** | PostgreSQL과 동일 | 가능하지만 운영 store 추가 |
| 깊은 경로·graph algorithms | 제한적 | 제한적 | 제한적 | **가장 적합** |
| 백업·감사·다중 worker | 단순하지만 단일 파일 제약 | **가장 균형적** | PostgreSQL 운영에 포함 | 추가 백업·동기화 필요 |
| 도구 제거 시 축소 | 매우 쉬움 | semantic/graph 기능만 비활성화 가능 | projection 삭제 가능 | projection 삭제 후 PostgreSQL로 fallback 필요 |

결론은 `PostgreSQL canonical + PostgreSQL lexical + pgvector production profile + optional Neo4j projection`이다. pgvector 설치와 semantic 활성화는 별개다. 참조 profile은 배포·migration 일관성을 위해 pgvector를 포함하지만, vector projection은 재생성 가능하고 품질·ACL·freshness·지연시간 gate 전에는 검색 기본 경로에서 비활성이다.

### 1.3 제품 인터페이스 선택

KIP v3의 기본 인터페이스는 웹 프론트엔드가 아니다.

```text
사용자 → AI agent → Skill → CLI/JSON → KIP
```

웹 UI, REST 서버, MCP 서버는 반드시 필요하지 않다. 다음 조건이 생기면 선택형 adapter로 추가한다.

- 비개발자 다수가 agent 없이 직접 검색해야 한다.
- 관계 후보를 하루 수백 건 이상 검토해야 한다.
- 외부 프로그램이 장기 연결 API를 요구한다.
- 조직 차원의 사용자·권한 관리 화면이 필요하다.

---

## 2. 배경

### 2.1 현재 환경

- Obsidian 볼트에는 사람이 작성한 업무 메모, 인물 노트, 매뉴얼, 일일 기록이 있다.
- 회사 NAS에는 약 5천 건 이상의 HWP, PDF, XLSX 중심 실무 문서가 있다.
- 회사 방침상 HWP 수정본과 PDF 표현본을 함께 저장하는 경우가 많다.
- DEVONthink는 PDF 및 일부 파일을 색인하지만 HWP와 XLSX 구조 활용에는 한계가 있다.
- Slack과 메일에는 문서가 만들어지기 전의 논의, 승인, 반려, 보완 요청, 결정 맥락이 존재한다.
- 사용자는 별도 검색 UI보다 Claude Code 같은 AI agent가 이 정보를 정확히 찾고 근거를 제시하게 하려 한다.

### 2.2 기존 접근의 한계

1. 폴더는 사람에게 유용하지만 문서 간 의미 관계와 효력 관계를 충분히 표현하지 못한다.
2. Obsidian의 문서별 프록시 노트는 5천 건 이상에서 수동 유지비가 커진다.
3. SQLite 단일 파일은 소규모 색인에는 적합하지만 복수 소스 동시 수집, RLS, 장기 worker, 대규모 리비전 관리가 추가되면 부담이 커진다.
4. 임베딩만 사용하는 RAG는 문서번호, 기관명, 과제번호, 셀 헤더처럼 정확한 문자열 검색에서 불필요하게 불확실하다.
5. 그래프 DB를 원장으로 삼으면 온톨로지와 승인 이력이 특정 제품에 종속될 수 있다.
6. HWP·PDF·XLSX·Slack·메일의 locator가 다르므로 단순 청크 모델만으로는 감사 가능한 근거를 만들기 어렵다.
7. 검색 스니펫만 agent에게 제공하면 최신 원본과 숫자·수식·문맥을 잘못 해석할 수 있다.

---

## 3. 제품 비전

> 회사의 파일, 메시지, 메일을 원본 그대로 보존하면서 검색 가능한 근거 단위와 검증 가능한 의미 관계로 정규화하고, AI agent가 질문에 맞는 자료를 찾아 정확한 위치까지 읽고 답하도록 한다.

KIP v3는 지식 위키가 아니라 다음 세 기능을 제공하는 **agent-first knowledge fabric**이다.

1. **찾기**: 파일명뿐 아니라 문서 본문, 엑셀 셀의 문자열, Slack 메시지, 이메일 내용으로 후보를 찾는다.
2. **확인하기**: PDF 페이지, HWP 구조 위치, XLSX 시트·셀 범위, Slack 메시지, 이메일 MIME part를 다시 읽는다.
3. **연결하기**: 문서·사람·기관·과제·결정·요구사항의 관계를 근거가 붙은 assertion으로 관리한다.

---

## 4. 목표와 비목표

### 4.1 제품 목표

- **G-01**. AI agent가 문서명이나 경로를 몰라도 내용으로 자료를 찾을 수 있어야 한다.
- **G-02**. 모든 중요한 답변은 원본 위치와 해시를 포함해야 한다.
- **G-03**. HWP/HWPX를 하나의 특정 parser에 종속하지 않고 구조적으로 읽을 수 있어야 한다.
- **G-04**. XLSX는 전체 파일을 얕게 색인하고 후보만 깊게 읽는 2계층 구조를 사용해야 한다.
- **G-05**. Slack과 메일의 수정·삭제·스레드·첨부파일을 리비전과 근거 단위로 보존해야 한다.
- **G-06**. 온톨로지 정의, 승인된 assertion, 검색·그래프·벡터 projection을 분리해야 한다.
- **G-07**. 모델이나 parser가 교체돼도 승인된 지식과 원본 식별자는 유지돼야 한다.
- **G-08**. 기본 운영은 로컬 또는 회사 승인 인프라에서 가능해야 한다.
- **G-09**. 웹 UI 없이 Shell CLI와 Skill만으로 전체 핵심 흐름을 수행할 수 있어야 한다.
- **G-10**. 데이터가 늘거나 기능이 줄어도 구조를 유지할 수 있어야 한다.

### 4.2 비목표

- **NG-01**. 원본 HWP, PDF, XLSX, Slack 메시지, 이메일을 자동 수정하지 않는다.
- **NG-02**. 모든 파일을 Markdown으로 영구 변환하지 않는다.
- **NG-03**. LLM의 추론을 자동으로 공식 사실로 승인하지 않는다.
- **NG-04**. 검색 스니펫만으로 재무 합계나 계약 효력을 확정하지 않는다.
- **NG-05**. Neo4j, Graphify, 특정 embedding provider를 필수 구성으로 만들지 않는다.
- **NG-06**. 초기에 전사 포털이나 범용 문서 관리 시스템을 만들지 않는다.
- **NG-07**. Apple Mail의 비공개 내부 DB를 직접 읽는 방식을 기본으로 사용하지 않는다.
- **NG-08**. 개인 메일과 회사 메일을 같은 workspace에 자동 병합하지 않는다.

---

## 5. 사용자와 역할

### 5.1 Primary actor: AI agent

Claude Code, Codex 또는 기타 agent가 검색·읽기·관계 탐색 명령을 호출한다.

Agent는 다음을 수행한다.

- 검색 인덱스의 최신 상태를 확인한다.
- 정확 식별자 검색과 전문 검색을 수행한다.
- 0건 또는 저신뢰 결과에서 실제 색인 어휘를 조회한다.
- 후보의 정확한 근거 단위를 읽는다.
- 숫자와 수식 질문은 XLSX 원본 범위를 다시 읽는다.
- 승인된 assertion과 제안 상태의 후보를 구분한다.
- 근거 locator와 stale 여부를 답변에 포함한다.

### 5.2 Operator

운영자는 소스 연결, 증분 동기화, parser 상태, 백업, 복구, projection 재구축을 담당한다.

### 5.3 Knowledge reviewer

검토자는 다음을 승인 또는 반려한다.

- 사람·기관·과제의 동일성 병합 후보
- `amends`, `supersedes`, `approves`, `evidences` 같은 고위험 관계
- 모델이 추출한 날짜, 금액, 비율, 의무와 같은 중요 사실

### 5.4 Human requester

사람 사용자는 자연어로 질문하고, agent가 제시한 근거를 검토한다. 직접 DB나 그래프 질의 언어를 알 필요가 없다.

---

## 6. 핵심 제품 원칙

| ID | 원칙 | 설명 |
|---|---|---|
| P-01 | Agent-first | CLI와 Skill을 먼저 설계하고 UI는 나중에 추가한다. |
| P-02 | Evidence-first | 답변보다 원본 위치와 근거 무결성을 우선한다. |
| P-03 | PostgreSQL as canonical store | 원본 식별, 리비전, ACL, assertion, 감사 상태는 PostgreSQL이 보유한다. |
| P-04 | Projections are disposable | FTS, vector, graph, 요약은 원장에서 재생성할 수 있어야 한다. |
| P-05 | Ontology is independent | 온톨로지는 특정 DB의 라벨·엣지 스키마가 아니다. |
| P-06 | Adapters over SDKs | Core는 외부 제품 SDK를 직접 호출하지 않는다. |
| P-07 | Shallow first, deep on demand | 전체 자료는 저비용 색인, 후보는 원본 정밀 읽기를 사용한다. |
| P-08 | Candidate before assertion | 모델·Graphify 출력은 후보이며 승인 후에만 공식 지식이 된다. |
| P-09 | Source ACL propagation | 원본보다 넓은 공개 범위로 파생 정보를 노출하지 않는다. |
| P-10 | Safe reprocessing | 새 parser나 모델 결과가 기존의 더 좋은 결과를 자동으로 훼손하지 않는다. |
| P-11 | Stable agent contracts | 내부 DB나 검색 도구가 바뀌어도 CLI/JSON 계약은 유지한다. |
| P-12 | Progressive complexity | 검증되지 않은 Neo4j·벡터·UI를 미리 운영하지 않는다. |

---

## 7. 제품 범위

### 7.1 Baseline scope

KIP v3 기준 릴리스는 다음을 포함한다.

- PostgreSQL 18 기반 canonical store
- `pg_trgm`과 사전 토큰화 기반 lexical search
- semantic projection contract와 참조 배포용 pgvector adapter; extension 미설치 상태에서도 필수 기능 동작
- NAS 파일 증분 색인
- HWP/HWPX parser broker
- PDF 페이지 추출 및 OCR fallback hook
- XLSX shallow index와 live range deep read
- Slack API 또는 export connector
- Apple Mail AppleScript connector와 IMAP/provider adapter 경계
- entity·assertion·evidence·ontology 모델
- PostgreSQL recursive graph adapter
- Shell CLI와 versioned JSON
- `AGENTS.md`, `CLAUDE.md`, project Skills
- 백업, export, rebuild, 상태 점검

### 7.2 Optional scope

- Neo4j graph projection
- Apache AGE adapter
- 외부 lexical search engine
- Graphify 또는 LLM relation miner
- local/cloud embedding provider
- MCP adapter
- REST adapter
- Web review UI
- S3-compatible object store

### 7.3 Supported source types

| Source | Baseline | Notes |
|---|---:|---|
| NAS filesystem | Yes | 읽기 전용, 상대경로와 hash 추적 |
| Slack internal app | Yes | 접근 가능한 conversation만 수집 |
| Slack export ZIP | Yes | 초기 backfill 또는 API 제약 보완 |
| Apple Mail | Yes | AppleScript backfill + rule spool |
| IMAP/provider API | Adapter | 가능하면 Apple Mail보다 우선 가능 |
| Obsidian Markdown | Yes | 사람 작성 지식 소스로 색인 가능 |
| DEVONthink | Link only | 편의용 원문 열기 포인터로만 사용 |

### 7.4 Supported file formats

| Format | Baseline behavior |
|---|---|
| HWP/HWPX | parser broker + paired PDF + 구조 locator |
| PDF | 페이지 단위 추출, 스캔 감지, OCR adapter |
| XLSX/XLSM | shallow index + candidate deep read |
| DOCX | 제목·문단·표 단위 추출 |
| PPTX | shape 단위 구조 추출 + 슬라이드/도형 locator |
| CSV/TSV | 헤더·행 샘플·필요 시 전체 구조 읽기 |
| Markdown/Text | 제목 구간 또는 블록 단위 추출 |
| EML/MIME | 헤더·본문·첨부파일·thread 관계 |
| Slack JSON | 메시지·스레드·파일·수정·삭제 리비전 |

---

## 8. 핵심 사용자 여정

### 8.1 문서 내용으로 찾기

사용자 질문:

> A과제 참여율 변경을 승인한 문서가 뭐야?

Agent 흐름:

1. 과제 ID와 관련 alias를 확인한다.
2. 문서번호·과제번호 exact search를 우선한다.
3. `참여율`, `변경`, `승인`을 lexical search한다.
4. 후보 문서의 유효 버전과 `amends` 관계를 조회한다.
5. PDF 페이지 또는 HWP locator를 다시 읽는다.
6. 결론, 문서 ID, 페이지, 원본 hash, stale 여부를 반환한다.

### 8.2 엑셀 셀 안의 문자열로 찾기

사용자 질문:

> ‘재방문율’이라는 열이 들어간 엑셀을 찾아줘.

Agent 흐름:

1. 전체 XLSX shallow index에서 시트명, dimension, shared strings 전문을 검색한다.
2. 0건이면 vocabulary를 조회하고 검색어를 확장한다.
3. 후보 3~5개의 시트 규모를 확인한다.
4. 작은 시트는 전체, 큰 시트는 헤더와 제한 범위를 원본에서 읽는다.
5. 파일 경로, 시트, 셀 범위를 반환한다.

### 8.3 엑셀 숫자 계산

사용자 질문:

> A과제 장비비 총액을 계산해줘.

Agent 흐름:

1. FTS는 후보 파일을 찾는 데만 사용한다.
2. 시트와 열을 식별한다.
3. 원본 XLSX를 읽기 전용으로 열고 실제 셀 값을 읽는다.
4. 계산에 사용한 시트·범위와 제외 규칙을 함께 반환한다.

### 8.4 Slack과 메일을 포함한 결정 맥락 찾기

사용자 질문:

> 참여율 변경 전에 어떤 논의가 있었는지 정리해줘.

Agent 흐름:

1. 공식 승인 문서의 날짜와 관련 엔티티를 확인한다.
2. 해당 날짜 이전 범위의 Slack과 메일을 검색한다.
3. Slack thread와 이메일 `In-Reply-To`를 따라 conversation을 복원한다.
4. `reply_to` 같은 기술 관계와 `approves` 같은 의미 관계를 구분한다.
5. 승인된 assertion만 공식 결론에 사용하고, 미승인 후보는 별도 표시한다.

### 8.5 parser 교체

1. 새 HWP parser를 shadow mode로 실행한다.
2. 기존 extraction과 구조·텍스트·표·오류율을 비교한다.
3. 품질 규칙을 통과한 결과만 active extraction 후보가 된다.
4. 기존 결과는 보존한다.
5. agent contract와 document ID는 바뀌지 않는다.

### 8.6 그래프 기능 확장

1. PostgreSQL assertion graph의 성능과 사용률을 측정한다.
2. Neo4j 도입 기준을 충족하면 approved assertion만 projection한다.
3. Neo4j 장애 시 PostgreSQL graph adapter로 자동 또는 수동 fallback한다.
4. Neo4j 삭제 후에도 canonical assertion은 손실되지 않는다.

---

## 9. 기능 요구사항

### 9.1 Platform and workspace

- **FR-PLT-001 MUST**: 시스템은 최소 하나의 workspace를 지원해야 한다.
- **FR-PLT-002 MUST**: 모든 canonical row는 `workspace_id`를 가져야 한다.
- **FR-PLT-003 MUST**: 회사 자료와 개인 자료는 별도 workspace 또는 별도 배포로 분리해야 한다.
- **FR-PLT-004 MUST**: 소스별 capability를 조회하는 명령을 제공해야 한다.
- **FR-PLT-005 MUST**: Core는 PostgreSQL, Neo4j, Slack, Apple Mail SDK 타입을 노출하지 않아야 한다.
- **FR-PLT-006 MUST**: 외부 adapter는 versioned manifest와 contract version을 선언해야 한다.
- **FR-PLT-007 SHOULD**: canonical JSONL export를 통해 다른 저장소로 이전할 수 있어야 한다.

### 9.2 Source synchronization

- **FR-SRC-001 MUST**: NAS 원본은 읽기 전용으로 스캔해야 한다.
- **FR-SRC-002 MUST**: 파일의 경로, 크기, 수정시각, content hash, format을 저장해야 한다.
- **FR-SRC-003 MUST**: 변경되지 않은 파일은 재추출하지 않아야 한다.
- **FR-SRC-004 MUST**: source root가 일시적으로 연결되지 않았을 때 전체 파일을 삭제로 처리하지 않아야 한다.
- **FR-SRC-005 MUST**: Slack connector는 cursor 또는 timestamp watermark 기반 증분 수집을 지원해야 한다.
- **FR-SRC-006 MUST**: Slack 수정 메시지는 새 revision으로 기록해야 한다.
- **FR-SRC-007 MUST**: Slack 삭제 메시지는 tombstone으로 기록해야 한다.
- **FR-SRC-008 MUST**: Slack의 conversation ACL과 source scope를 보존해야 한다.
- **FR-SRC-009 MUST**: Apple Mail connector는 account와 mailbox allowlist를 지원해야 한다.
- **FR-SRC-010 MUST**: 이메일은 RFC Message-ID를 우선 식별자로 사용해야 한다.
- **FR-SRC-011 MUST**: Message-ID가 없는 이메일은 안정된 fallback hash를 사용해야 한다.
- **FR-SRC-012 MUST**: 같은 이메일의 여러 mailbox placement를 메시지 중복과 구분해야 한다.
- **FR-SRC-013 SHOULD**: Slack export ZIP을 초기 backfill에 사용할 수 있어야 한다.
- **FR-SRC-014 SHOULD**: Apple Mail rule이 신규 메시지를 spool에 넣는 push 보조 경로를 제공해야 한다.
- **FR-SRC-015 MAY**: IMAP 또는 Gmail/Microsoft provider API adapter를 추가할 수 있어야 한다.

### 9.3 Raw capture and source identity

- **FR-RAW-001 MUST**: source revision은 immutable해야 한다.
- **FR-RAW-002 MUST**: Slack raw JSON과 이메일 EML은 정책에 따라 content-addressed object store에 보존할 수 있어야 한다.
- **FR-RAW-003 MUST**: 대형 NAS 원본 파일을 PostgreSQL bytea로 복제하지 않아야 한다.
- **FR-RAW-004 MUST**: 원본 URI와 hash는 evidence locator에서 추적 가능해야 한다.
- **FR-RAW-005 MUST**: 첨부파일은 독립 Artifact로 등록하고 부모 메시지와 연결해야 한다.

### 9.4 Parsing and extraction

- **FR-EXT-001 MUST**: parser는 canonical `DocumentPacket` 계약을 출력해야 한다.
- **FR-EXT-002 MUST**: parser 실패가 전체 sync를 중단시키지 않아야 한다.
- **FR-EXT-003 MUST**: extraction에는 parser 이름, 버전, 입력 hash, 실행시각, 경고, 품질점수를 저장해야 한다.
- **FR-EXT-004 MUST**: 새 extraction은 shadow 상태로 생성한 뒤 active pointer를 원자적으로 전환해야 한다.
- **FR-EXT-005 MUST**: 새 결과가 품질 기준을 충족하지 못하면 기존 active extraction을 유지해야 한다.
- **FR-EXT-006 MUST**: 페이지, 섹션, 표, 셀, 메시지, MIME part locator를 보존해야 한다.
- **FR-EXT-007 SHOULD**: 위험한 parser는 별도 process 또는 container에서 실행해야 한다.
- **FR-EXT-008 SHOULD**: parser별 conformance test를 제공해야 한다.

### 9.5 HWP/HWPX

- **FR-HWP-001 MUST**: HWP/HWPX는 하나의 parser에 하드코딩하지 않아야 한다.
- **FR-HWP-002 MUST**: 최소 primary parser와 fallback parser 계약을 지원해야 한다.
- **FR-HWP-003 MUST**: 본문, 제목, 문단, 표, 각주, 미주, 링크, 이미지 참조를 가능한 범위에서 구조화해야 한다.
- **FR-HWP-004 MUST**: 대응 PDF가 있으면 같은 논리 문서의 별도 representation으로 묶어야 한다.
- **FR-HWP-005 MUST**: HWP와 PDF를 서로 다른 개정본으로 오인하지 않도록 pairing confidence를 저장해야 한다.
- **FR-HWP-006 MUST**: PDF가 있으면 최종 사용자 인용은 PDF 페이지를 우선할 수 있어야 한다.
- **FR-HWP-007 MUST**: PDF가 없으면 section, paragraph, table, row, column 기반 HWP locator를 반환해야 한다.
- **FR-HWP-008 SHOULD**: 실제 회사 표본으로 parser benchmark를 수행해 active adapter를 결정해야 한다.
- **FR-HWP-009 SHOULD**: parser 간 불일치를 review queue로 보낼 수 있어야 한다.

### 9.6 PDF and OCR

- **FR-PDF-001 MUST**: PDF는 페이지 단위 content unit을 생성해야 한다.
- **FR-PDF-002 MUST**: 텍스트가 없는 페이지를 스캔 가능성으로 표시해야 한다.
- **FR-PDF-003 MUST**: OCR 결과는 native page를 대체하지 않는 `pdf_ocr` content unit으로 저장하고 composite parser identity를 남겨야 한다.
- **FR-PDF-004 MUST**: OCR이 원본 파일을 덮어쓰지 않아야 한다.
- **FR-PDF-005 SHOULD**: 표·다단 구조가 중요한 후보 문서는 정밀 parser로 재처리할 수 있어야 한다.
- **FR-PDF-006 MUST**: OCR 실패는 native page unit을 보존하고 extraction warning으로 드러나야 한다.
- **FR-PDF-007 MUST**: 한국어 OCR runtime과 모델은 정확한 버전으로 설치·검증되어야 하며 정상 indexing 중 실행 코드를 내려받지 않아야 한다.

### 9.7 PPTX structural extraction

- **FR-PPTX-001 MUST**: PPTX의 텍스트, 표, 차트, 이미지 참조, 그룹 도형, 발표자 노트를 서로 구분되는 content unit으로 구조화해야 한다.
- **FR-PPTX-002 MUST**: 모든 shape unit은 slide 번호·slide ID·shape ID·group path·EMU 좌표를 보존해야 한다.
- **FR-PPTX-003 MUST**: 표 병합 범위, 차트의 저장된 category/series/value, 텍스트 run과 hyperlink를 보존해야 한다.
- **FR-PPTX-004 MUST**: 숨김 슬라이드, 전환·animation 존재, legacy comment, SmartArt text, 생략된 embedded object와 media를 metadata 또는 warning으로 표시해야 한다.
- **FR-PPTX-005 MUST**: 매크로를 실행하거나 외부 relationship을 fetch하지 않아야 하며 원본을 수정하지 않아야 한다.
- **FR-PPTX-006 SHOULD**: OCR, audio/video transcription, OLE 내부 전개는 native shape parser를 대체하지 않는 별도 candidate adapter로 제공해야 한다.
- **FR-PPTX-007 MUST**: PPTX 이미지 OCR은 이미지 hash로 중복 인식을 억제하고 count·개별 byte·총 byte·최소 크기 제한을 적용해야 한다.
- **FR-PPTX-008 MUST**: `pptx_ocr` unit은 원본 slide·shape·group·EMU geometry와 OCR pixel bbox를 함께 보존해야 한다.

### 9.8 XLSX two-tier retrieval

- **FR-XLSX-001 MUST**: 전체 XLSX는 shallow index를 생성해야 한다.
- **FR-XLSX-002 MUST**: shallow index에는 파일 경로, 시트명, 시트 dimension, shared strings 전문, 주요 헤더가 포함돼야 한다.
- **FR-XLSX-003 MUST**: shared strings를 소수 키워드로 압축하지 않아야 한다.
- **FR-XLSX-004 MUST**: shallow index 크기 상한은 설정 가능해야 하며 잘림 여부를 표시해야 한다.
- **FR-XLSX-005 MUST**: 후보 파일은 원본에서 시트·셀 범위를 정밀하게 읽고, 값이 없는 셀도 포함해 요청한 직사각형과 같은 좌표·행·열 shape를 반환해야 한다.
- **FR-XLSX-006 MUST**: 숫자, 날짜, 시간, duration, 수식·캐시 결과, 합계는 shallow index가 아니라 live range read를 사용해야 하며 public 응답은 strict JSON scalar만 포함해야 한다.
- **FR-XLSX-007 MUST**: deep read는 원본을 읽기 전용으로 열어야 한다.
- **FR-XLSX-008 MUST**: 대형 시트는 dimension에 따라 헤더와 제한 행을 우선 읽어야 한다.
- **FR-XLSX-009 MUST**: agent 답변은 파일, 시트, 범위를 명시해야 한다.
- **FR-XLSX-010 SHOULD**: 반복 집계가 필요한 표는 별도 tabular projection으로 변환할 수 있어야 한다.
- **FR-XLSX-011 MUST**: deep read는 Excel worksheet 경계, 정방향 range, bounded cell count를 검증하고 formula source와 cached value를 구분해야 한다.

### 9.9 Lexical search

- **FR-LEX-001 MUST**: exact identifier search를 제공해야 한다.
- **FR-LEX-002 MUST**: title, document number, project, organization, author, date, source type 필터를 제공해야 한다.
- **FR-LEX-003 MUST**: 한국어 검색은 PostgreSQL 기본 FTS만 단독으로 의존하지 않아야 한다.
- **FR-LEX-004 MUST**: 사전 토큰화된 lexeme index와 raw text fallback을 제공해야 한다.
- **FR-LEX-005 MUST**: `pg_trgm` 기반 alias·오탈자·부분문자열 보조 검색을 제공해야 한다.
- **FR-LEX-006 MUST**: 색인에 실제 존재하는 vocabulary와 document frequency를 조회할 수 있어야 한다.
- **FR-LEX-007 MUST**: 검색 결과를 document 단위로 collapse할 수 있어야 한다.
- **FR-LEX-008 MUST**: 검색 결과는 score만이 아니라 matched field와 content unit을 반환해야 한다.
- **FR-LEX-009 MUST**: 검색 projection은 canonical content에서 재구축할 수 있어야 한다.
- **FR-LEX-010 SHOULD**: lexical backend를 PGroonga, Tantivy, OpenSearch 등으로 교체할 수 있어야 한다.

### 9.10 Semantic search and pgvector

- **FR-VEC-001 MUST**: embedding은 canonical fact가 아니라 projection으로 취급해야 한다.
- **FR-VEC-002 MUST**: embedding model, version, dimension, input hash, normalization을 저장해야 한다.
- **FR-VEC-003 MUST**: 모델 교체 시 기존 embedding을 즉시 삭제하지 않아야 한다.
- **FR-VEC-004 MUST**: vector search를 비활성화해도 전체 시스템이 동작해야 한다.
- **FR-VEC-005 MUST**: vector result에는 source ACL 필터를 적용해야 한다.
- **FR-VEC-006 SHOULD**: lexical과 vector 결과는 raw score 직접 비교가 아니라 RRF 또는 별도 reranker로 결합해야 한다.
- **FR-VEC-007 SHOULD**: semantic search 도입 전 golden query에서 실제 개선을 입증해야 한다.
- **FR-VEC-008 MUST**: 1024차원 프로덕션 reference projection은 HNSW cosine
  index를 사용하고, ACL/freshness filter 뒤 후보 부족을 막는 bounded iterative
  scan 설정을 가져야 한다. 승격 전 exact-search 비교로 recall trade-off를
  측정해야 한다.

### 9.11 Entity and identity

- **FR-ENT-001 MUST**: Person, Organization, Project, Document, Communication, Requirement, Decision, Task, Event를 최소 entity type으로 지원해야 한다.
- **FR-ENT-002 MUST**: entity는 여러 source identifier를 가질 수 있어야 한다.
- **FR-ENT-003 MUST**: 정확한 이메일과 Slack profile email 일치는 자동 연결할 수 있어야 한다.
- **FR-ENT-004 MUST**: 이름만 같은 경우 자동 병합하지 않아야 한다.
- **FR-ENT-005 MUST**: entity merge와 split 이력을 보존해야 한다.
- **FR-ENT-006 MUST**: source object 삭제가 canonical entity를 자동 삭제하지 않아야 한다.

### 9.12 Ontology and assertions

- **FR-ONT-001 MUST**: 온톨로지는 versioned YAML 또는 동등한 외부 계약으로 관리해야 한다.
- **FR-ONT-002 MUST**: ontology release는 entity type, predicate, domain, range, inverse, risk, review policy를 정의해야 한다.
- **FR-ONT-003 MUST**: `reply_to` 같은 source relation과 `responds_to` 같은 semantic relation을 구분해야 한다.
- **FR-ONT-004 MUST**: assertion은 subject, predicate, object/value, evidence, status, origin, valid time, recorded time, ontology version을 포함해야 한다.
- **FR-ONT-005 MUST**: 모델 또는 Graphify 출력은 candidate로 저장해야 한다.
- **FR-ONT-006 MUST**: 고위험 predicate는 사람 승인 없이는 approved 상태가 될 수 없어야 한다.
- **FR-ONT-007 MUST**: approved assertion은 projection rebuild로 손실되지 않아야 한다.
- **FR-ONT-008 MUST**: assertion은 여러 evidence locator를 가질 수 있어야 한다.
- **FR-ONT-009 MUST**: assertion의 접근범위는 근거의 접근범위보다 넓어질 수 없어야 한다.
- **FR-ONT-010 SHOULD**: ontology migration은 rename, split, merge, deprecate를 명시해야 한다.

### 9.13 Graph capability

- **FR-GRF-001 MUST**: baseline은 PostgreSQL assertion table과 recursive query로 graph traversal을 제공해야 한다.
- **FR-GRF-002 MUST**: agent에게 raw SQL, Cypher, vendor graph ID를 노출하지 않아야 한다.
- **FR-GRF-003 MUST**: graph result는 assertion ID와 evidence를 반환해야 한다.
- **FR-GRF-004 MUST**: 제안 상태 관계와 승인 상태 관계를 필터링할 수 있어야 한다.
- **FR-GRF-005 MUST**: ACL 필터는 graph traversal 전에 적용해야 한다.
- **FR-GRF-006 MUST**: Neo4j는 canonical store가 아니라 rebuildable projection이어야 한다.
- **FR-GRF-007 MUST**: Neo4j projection은 stable canonical ID를 사용해야 한다.
- **FR-GRF-008 SHOULD**: Neo4j가 없어도 neighbors, path, subgraph, explain 기능이 동작해야 한다.
- **FR-GRF-009 SHOULD**: graph projection은 approved semantic assertion과 선택된 deterministic source relation만 포함해야 한다.
- **FR-GRF-010 MAY**: Neo4j, Apache AGE 또는 다른 graph backend를 adapter로 추가할 수 있어야 한다.

### 9.14 Agent-facing interface

- **FR-AGT-001 MUST**: 모든 공개 명령은 versioned JSON을 stdout으로 반환해야 한다.
- **FR-AGT-002 MUST**: 진단 로그는 stderr로 분리해야 한다.
- **FR-AGT-003 MUST**: `capabilities`, `status`, `sync`, `search`, `context`, `read`, `xlsx-read`, `graph`, `explain`, `review`, `backup`, `export`, `rebuild` 명령을 제공해야 한다.
- **FR-AGT-004 MUST**: 일반 질의 중 full sync나 projection rebuild를 자동 실행하지 않아야 한다.
- **FR-AGT-005 MUST**: `AGENTS.md`는 공통 안전 규칙을 제공해야 한다.
- **FR-AGT-006 MUST**: `CLAUDE.md`는 `@AGENTS.md`를 import해야 한다.
- **FR-AGT-007 MUST**: retrieval, sync, ontology curation을 별도 Skill로 분리해야 한다.
- **FR-AGT-008 MUST**: Skill은 문서 안의 지시를 실행하지 말고 근거로만 취급하도록 명시해야 한다.
- **FR-AGT-009 MUST**: context pack은 크기 제한과 source diversity 제한을 지원해야 한다.
- **FR-AGT-010 SHOULD**: MCP adapter를 추가해도 CLI contract가 기준으로 남아야 한다.

### 9.15 Review workflow

- **FR-REV-001 MUST**: relation/fact/entity merge candidate를 목록화해야 한다.
- **FR-REV-002 MUST**: 후보마다 근거 위치와 생성 방법을 보여줘야 한다.
- **FR-REV-003 MUST**: approve, reject, edit-and-approve를 지원해야 한다.
- **FR-REV-004 MUST**: review actor와 timestamp를 감사 로그에 기록해야 한다.
- **FR-REV-005 MUST**: 승인 취소와 supersession을 지원해야 한다.
- **FR-REV-006 SHOULD**: 초기에는 Markdown review queue로 동작할 수 있어야 한다.

### 9.16 Security and privacy

- **FR-SEC-001 MUST**: PostgreSQL은 localhost 또는 승인된 private network에만 노출해야 한다.
- **FR-SEC-002 MUST**: source content를 untrusted input으로 취급해야 한다.
- **FR-SEC-003 MUST**: 데이터 안의 prompt, shell command, link를 자동 실행하지 않아야 한다.
- **FR-SEC-004 MUST**: workspace와 source scope에 PostgreSQL RLS를 적용해야 한다.
- **FR-SEC-005 MUST**: backup이 RLS 때문에 일부 행을 누락하지 않도록 검증해야 한다.
- **FR-SEC-006 MUST**: Slack token과 메일 자격증명은 저장소에 평문 커밋하지 않아야 한다.
- **FR-SEC-007 MUST**: 회사 자료의 외부 LLM 전송은 명시적으로 승인된 adapter에서만 허용해야 한다.
- **FR-SEC-008 MUST**: 암호화·서명 메일은 복호화 가능 여부와 보안 상태를 보존해야 한다.
- **FR-SEC-009 MUST**: 첨부파일 parser는 파일 크기, 압축폭탄, 경로 traversal 제한을 적용해야 한다.
- **FR-SEC-010 SHOULD**: 민감 source별 retention과 redaction policy를 지원해야 한다.

### 9.17 Operations and portability

- **FR-OPS-001 MUST**: 증분 sync와 full rebuild를 분리해야 한다.
- **FR-OPS-002 MUST**: 동시 sync 중복 실행을 막아야 한다.
- **FR-OPS-003 MUST**: 실패한 job은 재시도 가능하고 idempotent해야 한다.
- **FR-OPS-004 MUST**: PostgreSQL backup과 object store backup을 함께 수행해야 한다.
- **FR-OPS-005 MUST**: canonical JSONL export를 제공해야 한다.
- **FR-OPS-006 MUST**: lexical, vector, graph projection을 독립적으로 rebuild할 수 있어야 한다.
- **FR-OPS-007 MUST**: adapter 교체 시 shadow run과 결과 비교를 지원해야 한다.
- **FR-OPS-008 MUST**: structured status와 health report를 제공해야 한다.
- **FR-OPS-009 SHOULD**: macOS launchd를 통한 예약 sync를 지원해야 한다.
- **FR-OPS-010 SHOULD**: PostgreSQL major upgrade와 restore drill 절차를 문서화해야 한다.

---

## 10. 비기능 요구사항

### 10.1 성능

- **NFR-PERF-001 MUST**: 5천 파일 기준 증분 스캔은 변경이 없을 때 2분 이내를 목표로 한다.
- **NFR-PERF-002 MUST**: exact identifier query P95는 300ms 이내를 목표로 한다.
- **NFR-PERF-003 MUST**: lexical search P95는 2초 이내를 목표로 한다.
- **NFR-PERF-004 MUST**: depth 4 이하 graph query P95는 2초 이내를 목표로 한다.
- **NFR-PERF-005 SHOULD**: vector search 활성 시 top-20 P95는 2초 이내를 목표로 한다.
- **NFR-PERF-006 MUST**: context pack 생성은 기본 30,000자 또는 설정된 token budget을 넘지 않아야 한다.

### 10.2 신뢰성

- **NFR-REL-001 MUST**: source connector 재실행은 중복 canonical object를 만들지 않아야 한다.
- **NFR-REL-002 MUST**: parser 실패가 기존 active extraction을 훼손하지 않아야 한다.
- **NFR-REL-003 MUST**: projection 장애가 canonical write를 손상시키지 않아야 한다.
- **NFR-REL-004 MUST**: 모든 중요 write는 audit event를 생성해야 한다.
- **NFR-REL-005 MUST**: backup 복구 테스트를 분기별로 수행할 수 있어야 한다.

### 10.3 유지보수성

- **NFR-MNT-001 MUST**: domain/application 계층은 vendor SDK import를 금지해야 한다.
- **NFR-MNT-002 MUST**: adapter contract test가 모든 구현체에 공통 적용돼야 한다.
- **NFR-MNT-003 MUST**: schema, ontology, CLI contract는 각각 version을 가져야 한다.
- **NFR-MNT-004 SHOULD**: 주요 선택은 ADR로 기록해야 한다.
- **NFR-MNT-005 MUST**: 제품 동작, public contract, architecture, configuration,
  security, operations, parser/model/projection lifecycle 또는 알려진 한계가
  바뀌면 영향을 받는 canonical 문서와 현재 구현 상태를 같은 변경에서
  갱신해야 한다.

### 10.4 검색 품질

- **NFR-RET-001 MUST**: golden query에서 lexical Recall@10 90% 이상을 달성해야 한다.
- **NFR-RET-002 MUST**: XLSX 셀 문자열 질문 Recall@10 95% 이상을 목표로 한다.
- **NFR-RET-003 MUST**: 중요 답변의 locator 정확도 98% 이상을 목표로 한다.
- **NFR-RET-004 MUST**: stale source가 있는 답변은 100% 경고해야 한다.
- **NFR-RET-005 SHOULD**: vector search는 lexical baseline보다 유의미한 개선이 있을 때만 기본 활성화한다.
- **NFR-RET-006 MUST**: 공개 저장소 CI는 최소 100개 positive 검색 계약과
  ACL-negative 계약을 매 merge에 실행해야 한다. Synthetic portable gate는
  실제 조직 corpus 품질 승격 근거를 대체하지 않는다.
- **NFR-RET-007 MUST**: 보호된 private-corpus runner에서는 dataset 또는 corpus
  부재와 gate skip을 실패로 취급해야 한다.

### 10.5 보안

- **NFR-SEC-001 MUST**: 권한 없는 source object의 내용과 존재 여부를 반환하지 않아야 한다.
- **NFR-SEC-002 MUST**: graph path가 비공개 노드를 경유한다는 사실도 권한 없이는 노출하지 않아야 한다.
- **NFR-SEC-003 MUST**: 모든 외부 송신 adapter는 egress allowlist를 가져야 한다.

---

## 11. 검색 및 답변 정책

### 11.1 기본 검색 순서

```text
1. Exact identifier
2. Structured filter
3. Lexical search
4. Alias/vocabulary expansion
5. Approved graph traversal
6. Semantic search, if enabled
7. Exact evidence read
8. Answer with locator and freshness
```

### 11.2 답변 근거 정책

Agent는 다음을 지켜야 한다.

- 검색 결과 제목이나 snippet만으로 중요한 결론을 확정하지 않는다.
- PDF는 정확한 페이지를 읽는다.
- XLSX 숫자는 정확한 시트·범위를 읽는다.
- Slack은 메시지 timestamp와 thread root를 표시한다.
- 이메일은 Message-ID와 MIME part를 표시한다.
- relation candidate는 공식 사실처럼 표현하지 않는다.
- 원본 hash가 색인 hash와 다르면 stale 경고를 표시한다.
- 검색된 문서에 질문이 요구한 식별자, 수치 대상, 또는 focused fact가 실제로
  없으면 관련 단어가 있다는 이유만으로 성공 답변을 만들지 않고
  `answer_not_present`로 거절한다.
- 짧은 질문이 서로 다른 여러 문서에 동시에 걸려 하나의 근거를 선택할 수
  없으면 임의 선택 대신 `clarification_required`를 반환한다.

---

## 12. PostgreSQL, pgvector, Neo4j 도입 판단

### 12.1 PostgreSQL을 기준으로 선택한 이유

- Slack·메일·NAS worker의 동시 write를 안정적으로 처리한다.
- source revision과 assertion을 하나의 transaction으로 관리할 수 있다.
- RLS를 통해 workspace와 source scope를 강제할 수 있다.
- JSONB로 source별 가변 metadata를 수용하면서 핵심 컬럼은 정규화할 수 있다.
- `pg_trgm`, FTS, recursive CTE, pgvector를 같은 운영 단위에서 사용할 수 있다.
- `pg_dump`와 표준 클라이언트 생태계를 사용할 수 있다.

### 12.2 pgvector를 설치하되 기본 비활성화하는 이유

- 설치 비용은 낮지만 embedding 생성 비용과 모델 종속성은 별도다.
- 정확한 문서번호·기관명·헤더 검색에는 lexical search가 우선이다.
- 의미 검색은 표현이 완전히 다른 유사 사례를 찾는 데 유용하다.
- 모델이 바뀌면 projection을 병렬 생성해 평가한 뒤 전환할 수 있다.

### 12.3 Neo4j를 기본 구성에서 제외하는 이유

- 현재 핵심 질문은 대부분 1~4 hop의 제한된 관계 탐색이다.
- 원본, ACL, review, valid time, assertion evidence는 관계형 원장이 더 단순하다.
- Neo4j를 추가하면 데이터 복제, 동기화, 백업, 접근제어, 운영 감시가 늘어난다.
- 그래프 전용 질의와 알고리즘 수요가 입증된 뒤 projection으로 추가하는 편이 안전하다.

### 12.4 Neo4j 도입 기준

다음 중 둘 이상이 4주 이상 지속될 때 도입을 검토한다.

- 승인된 assertion이 200만 건 이상이다.
- depth 4 path query P95가 2초를 지속적으로 넘는다.
- 전체 agent 질의의 25% 이상이 graph traversal을 포함한다.
- 커뮤니티 탐지, 중심성, weighted shortest path 등 graph algorithm이 제품 요구가 된다.
- SQL recursive query 유지보수가 명확한 병목이 된다.
- 별도 graph team 또는 운영 책임자가 확보된다.

---

## 13. 성공 지표

### 13.1 Retrieval metrics

| Metric | Pilot target | Full target |
|---|---:|---:|
| Document Recall@10 | 90% | 95% |
| XLSX text Recall@10 | 95% | 98% |
| Evidence locator accuracy | 95% | 98% |
| Latest-version selection accuracy | 95% | 99% |
| False approved relation rate | 0% | 0% |

### 13.2 Agent metrics

- 질문 한 건당 평균 tool call 8회 이하
- 검색 0건 후 vocabulary 재검색 성공률 70% 이상
- 중요 답변의 citation 누락률 2% 미만
- XLSX 계산에서 range 미표시 비율 0%

### 13.3 Operational metrics

- 증분 sync 성공률 99% 이상
- source revision 중복률 0.1% 미만
- parser 실패율 5% 미만, 미처리 파일 100% 가시화
- backup restore drill 성공률 100%
- projection rebuild가 canonical row를 변경하는 사건 0건

---

## 14. 출시 단계

### Phase 0 — Architecture baseline

- PostgreSQL 18 + pgvector 개발 환경
- canonical schema와 RLS
- CLI JSON envelope
- AGENTS.md, CLAUDE.md, Skill skeleton

### Phase 1 — NAS document pilot

- 활성 연구과제 한 개
- HWP/PDF pairing
- PDF, HWP, XLSX shallow index
- lexical search, context, read, xlsx-read
- golden query 30~50개

### Phase 2 — Slack and mail

- Slack API/export backfill
- Slack thread, edit, delete
- Apple Mail account/mailbox allowlist
- EML, MIME, attachment pipeline
- source ACL propagation

### Phase 3 — Ontology and assertion

- entity identifiers and merge candidates
- source relations
- relation/fact candidates
- human approval flow
- PostgreSQL graph query

### Phase 4 — Semantic projection

- embedding provider adapter
- pgvector exact search
- hybrid evaluation
- 1024차원 HNSW production index와 exact-search recall 비교

### Phase 5 — Optional graph projection

- Neo4j adoption criteria 재검토
- projection adapter와 parity tests
- graph algorithm 요구가 있을 때만 배포

---

## 15. 주요 위험과 대응

| Risk | Impact | Mitigation |
|---|---|---|
| PostgreSQL 운영 부담 | 개인 환경에서 복잡성 증가 | Docker Compose, localhost bind, 자동 backup, one-command doctor |
| 한국어 FTS 품질 부족 | 검색 누락 | tokenization adapter + vocabulary + pg_trgm + golden query |
| HWP parser 품질 편차 | 본문·표 누락 | parser broker, paired PDF, shadow extraction, benchmark |
| PPTX 구조 손실 | 표·차트·노트가 평문에 섞임 | shape locator, typed metadata, OOXML 보조 파트, 실제 corpus gate |
| Slack API 제한 | backfill 지연 | internal app, cursor sync, export ZIP, rate-aware scheduler |
| Apple Mail 자동화 권한 | sync 실패 | Automation permission doctor, IMAP/provider fallback |
| 민감 메일 노출 | 보안 사고 | account/mailbox allowlist, RLS, workspace separation |
| embedding 모델 교체 | 전체 재색인 | versioned embedding space, parallel projection |
| graph DB 조기 도입 | 운영 복잡성 | PostgreSQL graph baseline, threshold-based adoption |
| LLM hallucination | 잘못된 결론 | evidence read, candidate/approved separation, citations |
| 원본 변경과 색인 불일치 | 오래된 답변 | live hash check and stale flag |

---

## 16. 제품 인수 기준

KIP v3 baseline은 다음을 모두 만족해야 인수된다.

1. PostgreSQL이 canonical store로 동작한다.
2. pgvector extension이 없거나 비활성 상태에서도 모든 필수 테스트가 통과하고, 참조 profile에서는 선택적으로 설치·활성화할 수 있다.
3. Neo4j 없이 graph 명령이 동작한다.
4. HWP/HWPX parser를 설정으로 교체할 수 있다.
5. HWP/PDF representation pairing이 지원된다.
6. XLSX shallow index가 shared strings와 sheet dimension을 포함한다.
7. XLSX 숫자 답변이 live range read를 사용한다.
8. Slack 메시지 수정·삭제·스레드가 리비전으로 저장된다.
9. Apple Mail 또는 IMAP에서 허용된 계정과 mailbox만 수집된다.
10. 모든 근거가 source-specific locator를 가진다.
11. 고위험 assertion은 사람 승인 없이는 approved가 될 수 없다.
12. RLS가 권한 없는 content와 graph path를 차단한다.
13. AGENTS.md와 Skills가 검색·읽기·인용 순서를 강제한다.
14. golden query 품질 목표를 충족한다.
15. backup, restore, export, projection rebuild가 검증된다.

---

## 17. 결정 기록

이 표는 현재 저장소에 실제 ADR 파일이 있는 결정만 나열한다. 번호 공백은
역사적 예약 또는 미작성 결정을 뜻하며, 존재하지 않는 ADR을 승인 근거로
인용하지 않는다.

| Decision ID | Decision | Status |
|---|---|---|
| ADR-001 | PostgreSQL is canonical | Accepted |
| ADR-002 | CLI, REST, and MCP are edge adapters | Accepted |
| ADR-003 | Neo4j is optional and non-canonical | Accepted |
| ADR-004 | XLSX uses shallow indexing and deep reads | Accepted |
| ADR-005 | Local-first hybrid retrieval is evaluation-gated | Accepted |
| ADR-017 | Native hwp-hwpx-parser is the HWP primary | Accepted |
| ADR-018 | Jina Hugging Face reranker remains an opt-in shadow adapter | Accepted |
| ADR-019 | Evidence-first quality control plane | Accepted |
| ADR-020 | Trusted identity and expiring ACL snapshots | Accepted |
| ADR-021 | Canonical classification and atomic model egress | Accepted |
| ADR-022 | Provider-neutral structured generation | Accepted |
| ADR-023 | Verified generated-answer orchestration | Accepted |
| ADR-024 | Typed ontology relation candidates | Accepted |
| ADR-025 | Reviewed ontology mining jobs | Accepted |
| ADR-026 | Approved graph answer context | Accepted |
| ADR-027 | Materialize ontology changes as reviewed assertion candidates | Accepted |
| ADR-028 | Persist redacted RAG decisions and export bounded telemetry | Accepted |
| ADR-029 | Bind end-to-end RAG gates to reviewed immutable datasets | Accepted |
| ADR-030 | Seal complete backups and preserve rebuildable lexical input | Accepted |
| ADR-031 | Guarded HWP re-extraction and local lexical reranking | Accepted |
| ADR-032 | Consent-based interaction memory and staged ontology discovery | Accepted |
| ADR-033 | Retrieval and authorization hardening | Accepted |
| ADR-034 | Promote the candidate-local BM25 reranker | Accepted |
| ADR-035 | Version semantic inputs and resume projection rebuilds | Accepted |
| ADR-036 | Fix retrieval stage order and gate corpus regressions | Accepted |
| ADR-037 | Align the production search contract and readiness gates | Accepted |
| ADR-038 | Make the ontology curation loop reviewable end to end | Accepted |
| ADR-039 | Reconcile filesystem deletions with a complete-scan grace policy | Accepted |
| ADR-040 | Make guided setup end in a runnable deployment | Accepted |
| ADR-041 | Structured PPTX extraction preserves presentation evidence | Accepted |
| ADR-042 | Korean OCR enriches candidate pages and presentation images | Accepted |

---

## 18. 용어

| Term | Definition |
|---|---|
| Source Object | 원본 시스템에서 식별 가능한 파일, 메시지, 이메일 등의 객체 |
| Source Revision | 특정 시점의 immutable 원본 상태 |
| Artifact | 파싱 가능한 파일 또는 첨부 표현물 |
| Logical Document | HWP와 PDF 같은 여러 representation을 묶는 의미상 문서 |
| Content Unit | 페이지, 섹션, 표, 메시지, MIME part 등 검색·인용 단위 |
| Evidence Locator | 원본에서 해당 근거의 위치를 재현하는 구조 |
| Entity | 사람, 기관, 과제, 문서, 결정 등 지식 그래프 노드 |
| Assertion | 근거가 붙은 사실 또는 관계 주장 |
| Candidate | 아직 검토되지 않은 추출 결과 |
| Projection | canonical state에서 재생성 가능한 FTS, vector, graph 데이터 |
| Embedding Space | 특정 모델·버전·차원으로 생성된 vector 집합 |
| Parser Broker | 여러 parser를 선택·비교·fallback하는 계층 |
| RLS | PostgreSQL Row-Level Security |

---

## 19. 참고 자료

- **[R1]** DB형, 「엑셀 파일을 LLM이 검색하게 만들기 — 시트 이름만 잡히던 색인을 셀 안 글자까지 뒤지는 검색엔진으로」, 2026-07-22. https://dbhyeong.github.io/blog/excel-files-searchable-db-llm-fts
- **[R2]** PostgreSQL 18 Documentation. https://www.postgresql.org/docs/18/
- **[R3]** PostgreSQL `pg_trgm`. https://www.postgresql.org/docs/18/pgtrgm.html
- **[R4]** PostgreSQL Row Security Policies. https://www.postgresql.org/docs/18/ddl-rowsecurity.html
- **[R5]** pgvector. https://github.com/pgvector/pgvector
- **[R6]** Neo4j Cypher Manual: constraints, paths, vector indexes. https://neo4j.com/docs/cypher-manual/current/
- **[R7]** Apache AGE. https://github.com/apache/age
- **[R8]** kordoc. https://github.com/chrisryugj/kordoc
- **[R9]** unhwp. https://github.com/iyulab/unhwp
- **[R10]** hwp-hwpx-parser. https://github.com/KimDaehyeon6873/hwp-hwpx-parser
- **[R11]** Slack Developer Documentation. https://docs.slack.dev/
- **[R12]** Apple Mail automation and MailKit documentation. https://support.apple.com/guide/mail/ and https://developer.apple.com/documentation/mailkit/
