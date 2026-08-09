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
        or answers.filesystem_sources is None
        or answers.model_provider is None
        or answers.database_secret_ref is None
        or answers.cas_path is None
        or answers.backup_path is None
        or answers.retention_days is None
        or answers.sync_schedule is None
        or answers.evaluation_dataset is None
        or answers.ontology_reviewers is None
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
        identity_owner=answers.identity_owner,
        source_ownership=answers.source_ownership,
        sources=sources,
        mounts=mounts,
        model_provider=answers.model_provider,
        model_egress_classifications=list(
            answers.model_egress_classifications or []
        ),
        model_secret_ref=answers.model_secret_ref,
        database_secret_ref=answers.database_secret_ref,
        cas_path=answers.cas_path,
        backup_path=answers.backup_path,
        retention_days=answers.retention_days,
        sync_schedule=answers.sync_schedule,
        evaluation_dataset=answers.evaluation_dataset,
        ontology_reviewers=answers.ontology_reviewers,
        generated_files=[
            "config/kip.generated.toml",
            "compose.generated.yaml",
        ],
        warnings=warnings,
    )
    return plan.model_copy(
        update={"plan_fingerprint": plan.calculate_fingerprint()}
    )


def _first_missing_question(answers: SetupAnswers) -> SetupQuestion | None:
    ordered = [
        ("workspace", answers.workspace),
        ("identity_mode", answers.identity_mode),
        ("identity_owner", answers.identity_owner),
        ("source_ownership", answers.source_ownership),
        ("filesystem_sources", answers.filesystem_sources),
        ("model_provider", answers.model_provider),
    ]
    for question_id, value in ordered:
        if value is None:
            return _question(question_id)
    if answers.model_provider in {"openai", "anthropic"}:
        if answers.model_egress_classifications is None:
            return _question("model_egress_classifications")
        if answers.model_secret_ref is None:
            return _question("model_secret_ref")
    trailing = [
        ("database_secret_ref", answers.database_secret_ref),
        ("cas_path", answers.cas_path),
        ("backup_path", answers.backup_path),
        ("retention_days", answers.retention_days),
        ("sync_schedule", answers.sync_schedule),
        ("evaluation_dataset", answers.evaluation_dataset),
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
    "model_provider": SetupQuestion(
        id="model_provider",
        prompt="생성 모델은 비활성화, OpenAI, Anthropic 중 무엇을 사용하나요?",
        answer_format="one choice",
        choices=["disabled", "openai", "anthropic"],
        why="모델 공급자와 외부 전송 정책을 명시적으로 고정합니다.",
    ),
    "model_egress_classifications": SetupQuestion(
        id="model_egress_classifications",
        prompt="선택한 원격 모델로 전송을 허용할 데이터 등급은 무엇인가요?",
        answer_format="JSON array",
        choices=["public", "internal", "confidential", "restricted"],
        example='["public"]',
        why="허용되지 않은 등급의 근거가 외부 모델로 나가는 것을 차단합니다.",
    ),
    "model_secret_ref": SetupQuestion(
        id="model_secret_ref",
        prompt="모델 API credential의 비밀 참조는 무엇인가요? 실제 값은 입력하지 마세요.",
        answer_format="env:, keychain:, or secret-manager: reference",
        example="env:KIP_OPENAI_API_KEY",
        why="셋업 상태와 생성 파일에 credential 원문을 남기지 않습니다.",
    ),
    "database_secret_ref": SetupQuestion(
        id="database_secret_ref",
        prompt="PostgreSQL URL의 비밀 참조는 무엇인가요? 실제 URL은 입력하지 마세요.",
        answer_format="env:, keychain:, or secret-manager: reference",
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
}
