from __future__ import annotations

from pathlib import Path

from kip.errors import ValidationError
from kip.setup.inventory import inspect_source
from kip.setup.models import (
    MountPlan,
    SetupAnswers,
    SetupInspection,
    SetupPlan,
    SetupQuestion,
    SourcePlan,
)


def inspect_setup(
    answers: SetupAnswers,
    *,
    project_root: Path,
) -> SetupInspection:
    question = _first_missing_question(answers)
    risks: list[str] = []
    if answers.model_provider in {"openai", "anthropic"}:
        risks.append(
            "remote generation remains blocked for data classifications not explicitly allowed"
        )
    return SetupInspection(
        complete=question is None,
        answers_fingerprint=answers.fingerprint(),
        questions=[question] if question else [],
        risks=risks,
    )


def build_setup_plan(
    answers: SetupAnswers,
    *,
    project_root: Path,
) -> SetupPlan:
    inspection = inspect_setup(answers, project_root=project_root)
    if not inspection.complete:
        missing = inspection.questions[0].id
        raise ValidationError(f"setup answers are incomplete: {missing}")
    if (
        answers.workspace is None
        or answers.identity_mode is None
        or answers.identity_owner is None
        or answers.source_ownership is None
        or answers.ontology_profile is None
        or answers.filesystem_sources is None
        or answers.model_provider is None
        or answers.database_secret_ref is None
        or answers.cas_path is None
        or answers.backup_path is None
        or answers.retention_days is None
        or answers.sync_schedule is None
        or answers.evaluation_dataset is None
        or answers.interaction_memory_mode is None
        or answers.ontology_reviewers is None
        or (
            answers.identity_mode == "proxy_jwt"
            and (
                answers.jwt_issuer is None
                or answers.jwt_audience is None
                or answers.jwt_jwks_url is None
                or answers.jwt_admin_groups is None
            )
        )
        or (
            answers.identity_mode == "api_key"
            and (
                answers.identity_api_key_secret_ref is None
                or answers.identity_admin_key_secret_ref is None
            )
        )
    ):
        raise ValidationError("setup answers are incomplete")

    sources = [
        SourcePlan(
            name=source.name,
            host_root=source.root,
            target_root=f"/sources/{source.name}",
            classification=source.classification,
            acl_scope=source.acl_scope,
            include_extensions=source.include_extensions,
            exclude_globs=source.exclude_globs,
            inventory=inspect_source(source),
        )
        for source in answers.filesystem_sources
    ]
    mounts = [
        MountPlan(
            source=source.host_root,
            target=source.target_root,
            read_only=True,
            purpose="source",
        )
        for source in sources
    ]
    mounts.extend(
        [
            MountPlan(
                source=answers.cas_path,
                target="/var/lib/kip/cas",
                read_only=False,
                purpose="cas",
            ),
            MountPlan(
                source=answers.backup_path,
                target="/var/lib/kip/backups",
                read_only=False,
                purpose="backup",
            ),
        ]
    )
    warnings = list(inspection.risks)
    if answers.evaluation_dataset == "none":
        warnings.append(
            "no private evaluation dataset is configured; production promotion is blocked"
        )
    plan = SetupPlan(
        plan_fingerprint="",
        answers_fingerprint=answers.fingerprint(),
        workspace=answers.workspace,
        identity_mode=answers.identity_mode,
        jwt_issuer=answers.jwt_issuer,
        jwt_audience=answers.jwt_audience,
        jwt_jwks_url=answers.jwt_jwks_url,
        jwt_admin_groups=list(answers.jwt_admin_groups or []),
        identity_api_key_secret_ref=answers.identity_api_key_secret_ref,
        identity_admin_key_secret_ref=answers.identity_admin_key_secret_ref,
        identity_owner=answers.identity_owner,
        source_ownership=answers.source_ownership,
        ontology_profile=answers.ontology_profile,
        sources=sources,
        mounts=mounts,
        model_provider=answers.model_provider,
        model_egress_classifications=list(
            answers.model_egress_classifications or []
        ),
        model_retention_policy=answers.model_retention_policy,
        model_secret_ref=answers.model_secret_ref,
        database_secret_ref=answers.database_secret_ref,
        cas_path=answers.cas_path,
        backup_path=answers.backup_path,
        retention_days=answers.retention_days,
        sync_schedule=answers.sync_schedule,
        evaluation_dataset=answers.evaluation_dataset,
        interaction_memory_mode=answers.interaction_memory_mode,
        ontology_reviewers=answers.ontology_reviewers,
        generated_files=[
            "config/kip.generated.toml",
            "config/kip.host.generated.toml",
            "compose.generated.yaml",
            ".mcp.json",
        ],
        warnings=warnings,
    )
    return plan.model_copy(
        update={"plan_fingerprint": plan.calculate_fingerprint()}
    )


def _first_missing_question(answers: SetupAnswers) -> SetupQuestion | None:
    ordered: list[tuple[str, object | None]] = [
        ("workspace", answers.workspace),
        ("identity_mode", answers.identity_mode),
    ]
    for question_id, value in ordered:
        if value is None:
            return _question(question_id)
    if answers.identity_mode == "proxy_jwt":
        jwt_questions: list[tuple[str, object | None]] = [
            ("jwt_issuer", answers.jwt_issuer),
            ("jwt_audience", answers.jwt_audience),
            ("jwt_jwks_url", answers.jwt_jwks_url),
            ("jwt_admin_groups", answers.jwt_admin_groups),
        ]
        for question_id, value in jwt_questions:
            if value is None:
                return _question(question_id)
    if answers.identity_mode == "api_key":
        key_questions: list[tuple[str, object | None]] = [
            ("identity_api_key_secret_ref", answers.identity_api_key_secret_ref),
            ("identity_admin_key_secret_ref", answers.identity_admin_key_secret_ref),
        ]
        for question_id, value in key_questions:
            if value is None:
                return _question(question_id)
    ordered = [
        ("identity_owner", answers.identity_owner),
        ("source_ownership", answers.source_ownership),
        ("ontology_profile", answers.ontology_profile),
        ("filesystem_sources", answers.filesystem_sources),
        ("model_provider", answers.model_provider),
    ]
    for question_id, value in ordered:
        if value is None:
            return _question(question_id)
    if answers.model_provider in {"openai", "anthropic"}:
        if answers.model_egress_classifications is None:
            return _question("model_egress_classifications")
        if answers.model_retention_policy is None:
            return _question("model_retention_policy")
        if answers.model_secret_ref is None:
            return _question("model_secret_ref")
    trailing: list[tuple[str, object | None]] = [
        ("database_secret_ref", answers.database_secret_ref),
        ("cas_path", answers.cas_path),
        ("backup_path", answers.backup_path),
        ("retention_days", answers.retention_days),
        ("sync_schedule", answers.sync_schedule),
        ("evaluation_dataset", answers.evaluation_dataset),
        ("interaction_memory_mode", answers.interaction_memory_mode),
        ("ontology_reviewers", answers.ontology_reviewers),
    ]
    for question_id, trailing_value in trailing:
        if trailing_value is None:
            return _question(question_id)
    return None


def _question(question_id: str) -> SetupQuestion:
    return _QUESTIONS[question_id]


_QUESTIONS = {
    "workspace": SetupQuestion(
        id="workspace",
        prompt="이 배포가 사용할 조직 workspace slug는 무엇인가요?",
        answer_format="lowercase kebab-case string",
        example="acme-rnd",
        why="public IDs, ACL scope, database isolation의 최상위 경계입니다.",
    ),
    "identity_mode": SetupQuestion(
        id="identity_mode",
        prompt="사용자 신원은 identity-aware proxy JWT와 API key 중 무엇으로 검증하나요?",
        answer_format="one choice",
        choices=["proxy_jwt", "api_key"],
        why="클라이언트가 보낸 임의 header를 신원으로 신뢰하지 않기 위해 필요합니다.",
    ),
    "identity_owner": SetupQuestion(
        id="identity_owner",
        prompt="신원 발급과 접근 철회를 책임지는 팀 또는 역할은 누구인가요?",
        answer_format="non-empty string",
        example="platform-security",
        why="ACL freshness와 접근 사고의 운영 책임자를 지정합니다.",
    ),
    "jwt_issuer": SetupQuestion(
        id="jwt_issuer",
        prompt="신뢰할 JWT issuer의 정확한 URL은 무엇인가요?",
        answer_format="HTTPS URL",
        example="https://identity.example.com/",
        why="토큰 서명만이 아니라 발급 주체도 고정합니다.",
    ),
    "jwt_audience": SetupQuestion(
        id="jwt_audience",
        prompt="KIP API가 요구할 JWT audience 값은 무엇인가요?",
        answer_format="non-empty string",
        example="kip-api",
        why="다른 서비스용 토큰의 재사용을 차단합니다.",
    ),
    "jwt_jwks_url": SetupQuestion(
        id="jwt_jwks_url",
        prompt="JWT 서명 키를 가져올 JWKS URL은 무엇인가요?",
        answer_format="HTTPS URL",
        example="https://identity.example.com/.well-known/jwks.json",
        why="검증 키 회전과 캐시 만료를 명시적으로 관리합니다.",
    ),
    "jwt_admin_groups": SetupQuestion(
        id="jwt_admin_groups",
        prompt="동기화와 assertion review 권한을 가질 identity group 목록은 무엇인가요?",
        answer_format="JSON array of group names",
        example='["kip-admins"]',
        why="관리 API 권한을 별도 공유 비밀이 아니라 검증된 신원 claim에서 파생합니다.",
    ),
    "identity_api_key_secret_ref": SetupQuestion(
        id="identity_api_key_secret_ref",
        prompt="bootstrap API key의 비밀 참조는 무엇인가요? 실제 값은 입력하지 마세요.",
        answer_format="env: environment variable reference",
        example="env:KIP_API_KEY",
        why="bootstrap credential 원문을 셋업 상태와 생성 파일에서 분리합니다.",
    ),
    "identity_admin_key_secret_ref": SetupQuestion(
        id="identity_admin_key_secret_ref",
        prompt="bootstrap 관리 key의 별도 비밀 참조는 무엇인가요? 실제 값은 입력하지 마세요.",
        answer_format="env: environment variable reference",
        example="env:KIP_ADMIN_KEY",
        why="일반 검색 credential과 관리 작업 credential을 분리합니다.",
    ),
    "source_ownership": SetupQuestion(
        id="source_ownership",
        prompt="이 배포는 회사 자료와 개인 자료 중 어느 한쪽만 수집하나요?",
        answer_format="one choice",
        choices=["company", "personal"],
        why="개인 자료와 회사 자료를 동일 deployment에 섞지 않습니다.",
    ),
    "filesystem_sources": SetupQuestion(
        id="filesystem_sources",
        prompt="수집할 하위 폴더별 이름, 절대경로, 데이터 등급, ACL scope, 포함 확장자와 제외 glob을 알려주세요.",
        answer_format="JSON array of source objects",
        example='[{"name":"company-docs","root":"/mnt/nas/team","classification":"internal","acl_scope":"workspace:acme-rnd"}]',
        why="과도한 폴더 수집을 막고 실제 수집 범위를 미리 계산합니다.",
    ),
    "ontology_profile": SetupQuestion(
        id="ontology_profile",
        prompt="새 배포는 빈 도메인 프로파일과 연구과제 예제 프로파일 중 무엇으로 시작하나요?",
        answer_format="one choice",
        choices=["empty", "research-project"],
        why="의미 계약의 최소 커널은 유지하면서 도메인 전제를 명시적으로 선택합니다.",
    ),
    "model_provider": SetupQuestion(
        id="model_provider",
        prompt="생성 모델은 비활성화, 로컬, OpenAI, Anthropic 중 무엇을 사용하나요?",
        answer_format="one choice",
        choices=["disabled", "local", "openai", "anthropic"],
        why="모델 공급자와 외부 전송 정책을 명시적으로 고정합니다.",
    ),
    "model_egress_classifications": SetupQuestion(
        id="model_egress_classifications",
        prompt="선택한 원격 모델로 전송을 허용할 데이터 등급은 무엇인가요?",
        answer_format="JSON array",
        choices=["public", "internal", "confidential", "restricted", "personal"],
        example='["public"]',
        why="허용되지 않은 등급의 근거가 외부 모델로 나가는 것을 차단합니다.",
    ),
    "model_retention_policy": SetupQuestion(
        id="model_retention_policy",
        prompt="선택한 원격 모델 계약의 데이터 보존 정책은 무엇인가요?",
        answer_format="one choice",
        choices=["provider_default", "zero_retention"],
        why="비공개 자료는 zero-retention 계약이 확인된 경우에만 원격 전송합니다.",
    ),
    "model_secret_ref": SetupQuestion(
        id="model_secret_ref",
        prompt="모델 API credential의 비밀 참조는 무엇인가요? 실제 값은 입력하지 마세요.",
        answer_format="env: or file: reference",
        example="env:KIP_OPENAI_API_KEY",
        why="셋업 상태와 생성 파일에 credential 원문을 남기지 않습니다.",
    ),
    "database_secret_ref": SetupQuestion(
        id="database_secret_ref",
        prompt="PostgreSQL URL의 비밀 참조는 무엇인가요? 실제 URL은 입력하지 마세요.",
        answer_format="env: environment variable reference",
        example="env:KIP_DATABASE_URL",
        why="canonical store credential을 설정 파일과 셋업 기록에서 분리합니다.",
    ),
    "cas_path": SetupQuestion(
        id="cas_path",
        prompt="content-addressed storage를 둘 전용 절대경로는 어디인가요?",
        answer_format="absolute directory path",
        example="/srv/kip/cas",
        why="원본 snapshot과 canonical metadata의 수명주기를 분리합니다.",
    ),
    "backup_path": SetupQuestion(
        id="backup_path",
        prompt="암호화된 backup을 둘 전용 절대경로는 어디인가요?",
        answer_format="absolute directory path",
        example="/srv/kip/backups",
        why="복구 시험과 운영 데이터 보존에 필요합니다.",
    ),
    "retention_days": SetupQuestion(
        id="retention_days",
        prompt="운영 데이터 보존 기간은 며칠인가요?",
        answer_format="integer from 1 to 36500",
        example="365",
        why="삭제와 backup 정책을 명시적으로 정합니다.",
    ),
    "sync_schedule": SetupQuestion(
        id="sync_schedule",
        prompt="수집 주기는 5-field cron 또는 manual 중 무엇인가요?",
        answer_format="cron string or manual",
        example="0 * * * *",
        why="검색 경로가 암묵적으로 전체 sync를 시작하지 않게 합니다.",
    ),
    "evaluation_dataset": SetupQuestion(
        id="evaluation_dataset",
        prompt="검토된 내부 평가 dataset 경로가 있나요? 없으면 none이라고 답하세요.",
        answer_format="absolute file path or none",
        example="none",
        why="운영 승격 판단과 현재 품질 한계를 분리합니다.",
    ),
    "ontology_reviewers": SetupQuestion(
        id="ontology_reviewers",
        prompt="온톨로지와 assertion 승인을 책임질 reviewer 목록은 누구인가요?",
        answer_format="JSON array of stable role or principal identifiers",
        example='["knowledge-owner@example.invalid"]',
        why="추출된 관계가 자동으로 사실로 승격되지 않게 합니다.",
    ),
    "interaction_memory_mode": SetupQuestion(
        id="interaction_memory_mode",
        prompt="사용자 확인 기반 선호 기억과 온톨로지 발견 후보를 보존할까요?",
        answer_format="one choice",
        choices=["disabled", "explicit_consent"],
        why="질의·답변·원문을 자동 보존하지 않고 명시적으로 동의한 설정만 기억합니다.",
    ),
}
