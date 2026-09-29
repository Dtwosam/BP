from __future__ import annotations

import hashlib
import importlib.util
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from functools import lru_cache
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
_APPROVAL_SOURCE = (
    _REPO_ROOT / "src" / "bp_engine" / "execution" / "telegram_approval.py"
)
_NONCE = re.compile(r"\A[A-Za-z0-9_-]{16}\Z")
_NUMBER = r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?"
_READY_PROMPT = re.compile(
    r"\A"
    r"BP V3 LIVE TRADE READY\n\n"
    r"Side: (UP|DOWN)\n"
    rf"Limit: ({_NUMBER})\n"
    rf"Shares: ({_NUMBER})\n"
    rf"Maximum spend: \$({_NUMBER})\n"
    r"Time remaining: ([0-9]+\.[0-9])s\n\n"
    r"Approve only if you want this exact real-money order submitted\.\Z"
)
_CANDIDATE_PROMPT = re.compile(
    r"\A"
    r"BP V3 LIVE TRADE CANDIDATE\n\n"
    r"Side: (UP|DOWN)\n"
    rf"Limit: ({_NUMBER})\n"
    rf"Shares: ({_NUMBER})\n"
    rf"Maximum spend: \$({_NUMBER})\n"
    r"Time remaining: ([0-9]+\.[0-9])s\n\n"
    r"Final live risk and Johannesburg execution checks are still running\. "
    r"Approval does not bypass them\.\n\n"
    r"Approve only if you want this exact real-money order submitted "
    r"when every final gate passes\.\Z"
)
_LISTENER_APPROVAL_EDIT = re.compile(
    r"\ABP V3 trade APPROVED\nIntent: ([^\r\n]+)\nDecision time: ([^\r\n]+)\Z"
)
# git hash-object of the reviewed src/bp_engine/execution/telegram_approval.py.
# A later edit must update this pin only after the auto-approver is reviewed again.
APPROVAL_CONTRACT_BLOB_SHA = "5676efcb60840f4533a7f43b3c6a7efab9e97541"
LOCAL_DEADLINE_SAFETY_MARGIN_SECONDS = 2


class ContractSourceError(RuntimeError):
    pass


class ContractMismatch(RuntimeError):
    code = "APPROVAL_CONTRACT_MISMATCH"

    def __init__(self, actual_blob: str | None = None) -> None:
        self.actual_blob = actual_blob
        super().__init__(self.code)


@dataclass(frozen=True)
class CallbackButton:
    text: str
    callback_data: bytes | None = None
    url: str | None = None


@dataclass(frozen=True)
class PromptMatch:
    side: str
    limit_price: str
    shares: str
    maximum_spend: str
    time_remaining: Decimal


@dataclass(frozen=True)
class ListenerApprovalEdit:
    intent_id: str
    decision_at: datetime


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def verify_approval_contract(path: Path | None = None) -> str:
    """Refuse to run unless the approval file is the reviewed blob."""
    source = _APPROVAL_SOURCE if path is None else path
    if not source.is_file():
        raise ContractMismatch(None)
    actual = git_blob_sha1(source.read_bytes())
    if actual != APPROVAL_CONTRACT_BLOB_SHA:
        raise ContractMismatch(actual)
    return actual


@lru_cache(maxsize=1)
def _load_approval_module() -> ModuleType:
    """Load the Phase 15 approval module without importing the trading package."""
    if not _APPROVAL_SOURCE.is_file():
        raise ContractSourceError("telegram approval source is missing")
    spec = importlib.util.spec_from_file_location(
        "bp_phase15_telegram_approval_contract_source",
        _APPROVAL_SOURCE,
    )
    if spec is None or spec.loader is None:
        raise ContractSourceError("telegram approval source cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    required = (
        "MAX_APPROVAL_LIFETIME_SECONDS",
        "MIN_APPROVAL_WINDOW_SECONDS",
        "SUBMIT_SAFETY_FLOOR_SECONDS",
        "TARGET_NOTIONAL_USD",
        "build_candidate_prompt",
        "callback_data",
    )
    for name in required:
        if not hasattr(module, name):
            raise ContractSourceError(f"telegram approval source missing {name}")
    for name in (
        "MAX_APPROVAL_LIFETIME_SECONDS",
        "MIN_APPROVAL_WINDOW_SECONDS",
        "SUBMIT_SAFETY_FLOOR_SECONDS",
    ):
        if type(getattr(module, name)) is not int:
            raise ContractSourceError(f"{name} must be an int")
    if not isinstance(module.TARGET_NOTIONAL_USD, Decimal):
        raise ContractSourceError("TARGET_NOTIONAL_USD must be a Decimal")
    return module


def approval_source() -> ModuleType:
    verify_approval_contract()
    return _load_approval_module()


def parse_prompt(text: str | None) -> PromptMatch | None:
    if not isinstance(text, str):
        return None
    match = _READY_PROMPT.fullmatch(text)
    if match is None:
        match = _CANDIDATE_PROMPT.fullmatch(text)
    if match is None:
        return None
    side, limit_price, shares, spend, remaining_text = match.groups()
    source = approval_source()
    try:
        limit = Decimal(limit_price)
        share_count = Decimal(shares)
        spend_value = Decimal(spend)
        remaining = Decimal(remaining_text)
    except ArithmeticError:
        return None
    if (
        not limit.is_finite()
        or not share_count.is_finite()
        or not spend_value.is_finite()
        or not remaining.is_finite()
    ):
        return None
    if spend_value != source.TARGET_NOTIONAL_USD:
        return None
    if not Decimal("0") < limit <= Decimal("1"):
        return None
    if share_count <= 0:
        return None
    if remaining < Decimal(source.MIN_APPROVAL_WINDOW_SECONDS):
        return None
    return PromptMatch(
        side=side,
        limit_price=format(limit, "f"),
        shares=format(share_count, "f"),
        maximum_spend=format(source.TARGET_NOTIONAL_USD, "f"),
        time_remaining=remaining,
    )


def approval_deadline(sent_at: datetime, time_remaining: Decimal) -> datetime:
    if sent_at.tzinfo is None or sent_at.utcoffset() is None:
        raise ValueError("sent_at must be timezone-aware")
    source = approval_source()
    lifetime = min(
        Decimal(int(source.MAX_APPROVAL_LIFETIME_SECONDS)),
        time_remaining - Decimal(int(source.SUBMIT_SAFETY_FLOOR_SECONDS)),
    )
    if lifetime <= 0:
        raise ValueError("approval window is closed")
    microseconds = int(
        (lifetime * Decimal(1_000_000)).to_integral_value(rounding=ROUND_DOWN)
    )
    # The listener stamps expires_at before sendMessage. Message date is later,
    # so this client stops 2 seconds sooner and still clicks immediately when valid.
    deadline = sent_at.astimezone(UTC) + timedelta(microseconds=microseconds)
    deadline -= timedelta(seconds=LOCAL_DEADLINE_SAFETY_MARGIN_SECONDS)
    if deadline <= sent_at.astimezone(UTC):
        raise ValueError("approval window is closed")
    return deadline


def classify_keyboard(
    buttons: tuple[tuple[CallbackButton, ...], ...] | None,
) -> tuple[str | None, str]:
    """Return the shared nonce, or a stable rejection reason."""
    if buttons is None:
        return None, "keyboard_unreadable"
    if not _contains_approve(buttons):
        return None, "no_approve_button"
    if len(buttons) != 1 or len(buttons[0]) != 2:
        return None, "keyboard_shape_mismatch"
    approve, skip = buttons[0]
    if approve.text != "APPROVE" or skip.text != "SKIP":
        return None, "keyboard_text_mismatch"
    if approve.url or skip.url:
        return None, "keyboard_url_present"
    if not isinstance(approve.callback_data, bytes) or not isinstance(skip.callback_data, bytes):
        return None, "keyboard_callback_type_mismatch"
    try:
        approve_text = approve.callback_data.decode("utf-8")
        skip_text = skip.callback_data.decode("utf-8")
    except UnicodeError:
        return None, "keyboard_callback_decode_failed"
    nonce = _nonce_from_approve(approve_text)
    if nonce is None:
        return None, "keyboard_approve_nonce_mismatch"
    source = approval_source()
    try:
        expected_approve = source.callback_data("approve", nonce)
        expected_skip = source.callback_data("skip", nonce)
    except Exception:
        return None, "keyboard_expected_callback_error"
    if approve_text != expected_approve:
        return None, "keyboard_approve_callback_mismatch"
    if skip_text != expected_skip:
        return None, "keyboard_skip_callback_mismatch"
    if approve.callback_data != expected_approve.encode("ascii"):
        return None, "keyboard_approve_bytes_mismatch"
    if skip.callback_data != expected_skip.encode("ascii"):
        return None, "keyboard_skip_bytes_mismatch"
    return nonce, ""


def is_exact_approve_callback(data: bytes, nonce: str) -> bool:
    if not isinstance(data, bytes) or _NONCE.fullmatch(nonce) is None:
        return False
    source = approval_source()
    try:
        expected = source.callback_data("approve", nonce).encode("ascii")
    except Exception:
        return False
    return data == expected and not data.startswith(b"skip:")


def parse_listener_approval_edit(text: str | None) -> ListenerApprovalEdit | None:
    if not isinstance(text, str):
        return None
    match = _LISTENER_APPROVAL_EDIT.fullmatch(text)
    if match is None:
        return None
    intent_id, decision_text = match.groups()
    if not intent_id.strip() or intent_id != intent_id.strip():
        return None
    try:
        decision_at = datetime.fromisoformat(decision_text)
    except ValueError:
        return None
    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        return None
    return ListenerApprovalEdit(
        intent_id=intent_id,
        decision_at=decision_at.astimezone(UTC),
    )


def _nonce_from_approve(value: str) -> str | None:
    prefix = "approve:"
    if not value.startswith(prefix):
        return None
    nonce = value[len(prefix) :]
    if _NONCE.fullmatch(nonce) is None or ":" in nonce:
        return None
    return nonce


def _contains_approve(buttons: tuple[tuple[CallbackButton, ...], ...]) -> bool:
    for row in buttons:
        for button in row:
            if button.text == "APPROVE":
                return True
            data = button.callback_data
            if isinstance(data, bytes) and data.startswith(b"approve:"):
                return True
    return False
