# Evidence-bounded RAG reinforcement design

## Scope

이번 증분은 KIP를 retrieval-only fabric에서 안전한 answer surface로 확장한다. 외부 LLM, OCR, visual index를 기본 활성화하지 않고 이후 adapter가 따라야 할 공용 계약과 평가 경로를 먼저 만든다.

## Decisions

1. `answer`는 application service다. CLI, REST, MCP가 같은 서비스를 사용한다.
2. 답변은 검색 hit가 아니라 exact `EvidenceRead`에서만 구성한다.
3. source가 stale이거나 읽을 수 없으면 해당 근거를 답변에 사용하지 않는다.
4. admissible evidence가 없으면 명시적으로 refusal을 반환한다.
5. baseline generator는 deterministic extractive answer다. 원문을 인용 가능한 bounded passage로 반환하며 추론적 사실을 만들지 않는다.
6. 향후 LLM generator는 port/adapter로 추가하되 claim-to-evidence citation contract를 만족해야 한다.
7. 검색은 assertion을 만들지 않는다. 관계 후보는 별도 명시적 command에서 stable fingerprint로 idempotent하게 저장한다.
8. private scenario cases는 질문, expected evidence, refusal, ACL principal을 versioned data로 기록한다.

## Answer contract

`AnswerRequest`는 query, evidence limit, max characters를 받는다. `AnswerResponse`는 answer text, refused, citations, retrieval metadata를 반환한다. Citation은 unit ID, artifact ID, source URI, locator, indexed/current hash, stale flag를 포함한다.

## Safety

- ACL은 repository retrieval 전에 적용된다.
- source body는 untrusted evidence이며 generator instruction으로 취급하지 않는다.
- stale evidence는 citation에서 제외한다.
- XLSX 숫자 질문은 shallow answer를 허용하지 않고 exact `xlsx-read`가 필요하다는 refusal reason을 반환한다.
- normal answer/search는 sync, rebuild, graphify를 실행하지 않는다.

## Evaluation

무맥락 agent에게 자연어 요청만 주고 CLI discovery, evidence read, refusal, XLSX deep read, graph empty-state, ACL denial을 관찰한다. 동일 시나리오를 구현 전후 반복하며 machine-readable answer contract와 실제 사용자 답변을 함께 판정한다.
