# KIP 발표 자료 디자인 시스템

## 0. Research Log

- Embedded refs: Notion, Linear, Mintlify 후보를 검토하고, `minimalist-skill` + Linear의 정보 위계만 참고했다. 발표 주제가 근거와 신뢰이므로 장식보다 읽기 쉬운 편집물 방향을 택했다.
- Lazyweb: `knowledge management document search desktop`, `research repository document evidence desktop` 두 검색으로 문서 목록·검색 입력·상태 정보가 과밀하지 않은 화면 구성을 확인했다. 검색 화면을 복제하지 않고, 자료의 흐름을 보여 주는 발표형 레이아웃으로 재구성했다.
- Imagen drafts: 외부 이미지 없이도 발표의 핵심을 더 정확히 전달할 수 있어 사용하지 않았다. 벡터 검색 설명은 첨부된 좌표 화면의 교육적 구성을 참고하되, 발표의 색상·용어·근거 기준에 맞춘 네이티브 SVG 도해로 재구성했다.

## 1. Atmosphere & Identity

차분한 조사 노트와 업무 브리핑의 중간. 따뜻한 종이 바탕 위에 잉크색 글자와 짙은 남색의 근거선을 사용한다. 기억에 남는 장면은 흩어진 자료가 하나의 읽을 수 있는 근거 흐름으로 정리되는 모습이며, 기술 설명에서는 벡터 방향·입력 흐름·자료 유형별 경계를 네이티브 도해로 보여 준다.

## 2. Color

| 역할 | 토큰 | 값 | 용도 |
| --- | --- | --- | --- |
| 바탕 | `--canvas` | `#F6F2EA` | 슬라이드 배경 |
| 표면 | `--paper` | `#FFFDF8` | 카드·문서 단위 |
| 잉크 | `--ink` | `#1F2933` | 제목·본문 |
| 보조 잉크 | `--muted` | `#65707A` | 설명·메타 정보 |
| 선 | `--line` | `#D8D0C3` | 구분선 |
| 주색 | `--accent` | `#294C60` | 강조·진행 상태 |
| 연한 주색 | `--accent-pale` | `#DDE8E7` | 정보 배경 |
| 확인 | `--success` | `#476E55` | 승인·준비 상태 |
| 주의 | `--warning` | `#9A6630` | 파일럿 조건 |

주색은 탐색·강조에만 사용하며, 의미는 언제나 텍스트로 함께 표기한다.

## 3. Typography

| 단계 | 크기 | 굵기 | 용도 |
| --- | --- | --- | --- |
| Display | 68px | 700 | 표지·핵심 문장 |
| H1 | 48px | 700 | 슬라이드 제목 |
| H2 | 30px | 650 | 영역 제목 |
| Lead | 24px | 450 | 핵심 설명 |
| Body | 18px | 400 | 설명 문장 |
| Caption | 13px | 600 | 출처·레이블 |

- Sans: `Pretendard, SUIT, "Apple SD Gothic Neo", system-ui, sans-serif`
- Serif: `"Iowan Old Style", "Noto Serif KR", Georgia, serif`
- Mono: `ui-monospace, "SFMono-Regular", Menlo, monospace`

## 4. Spacing & Layout

기본 단위는 8px이며 `--space-1`부터 `--space-10`까지 8px 배수로 구성한다. 데스크톱은 16:9 고정 장면, 좁은 화면은 세로 읽기 모드로 전환한다. 모든 슬라이드는 72px 이상의 여백을 유지한다.

## 5. Components

### Slide
- 구조: 헤더, 주 내용, 출처/페이지 번호
- 상태: 현재/이전/다음/축소 화면
- 접근성: `role="group"`, 슬라이드 제목 레이블, 키보드 이동

### Evidence Card
- 구조: 작은 레이블, 제목, 짧은 설명, 위치 정보
- 상태: 기본/강조
- 접근성: 색상만으로 상태를 표현하지 않음

### Flow Step
- 구조: 순번, 동사, 한 줄 설명
- 상태: 기본/현재 강조
- 접근성: 흐름은 번호와 연결선으로 함께 표현

### Deck Controls
- 구조: 이전·다음 버튼, 진행 막대, 페이지 번호
- 상태: hover, focus, disabled
- 접근성: 44px 이상 터치 영역, 포커스 테두리, 좌우 화살표 및 스페이스 지원

## 6. Motion & Interaction

슬라이드 전환은 260ms `transform`과 `opacity`만 사용한다. 키보드·마우스·버튼으로 앞뒤 이동할 수 있으며, `prefers-reduced-motion`이면 즉시 전환한다.

## 7. Depth & Surface

`borders-only` 전략: 종이 표면과 얇은 선으로 층위를 나누며, 그림자는 쓰지 않는다. 배경의 미세한 격자와 여백이 깊이를 만든다.

## 8. Accessibility Constraints & Accepted Debt

- WCAG 2.2 AA를 목표로 한다. 본문 대비, 눈에 보이는 포커스, 키보드 전환, 축소 모션을 제공한다.
- 375px, 768px, 1280px에서 가로 스크롤 없이 읽을 수 있어야 한다.
- 수용한 부채 없음.
