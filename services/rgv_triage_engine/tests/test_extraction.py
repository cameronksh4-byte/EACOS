"""instructor + Bedrock extraction with a fake Mantle client."""

from __future__ import annotations

from typing import Any

import anyio
import pytest
from anthropic.types import Message

from conftest import PII_KEY
from triage_engine.config import Settings
from triage_engine.security.pii import PIIMasker
from triage_engine.services.extraction import (
    BedrockExtractionService,
    ExtractionError,
    UnsupportedMediaTypeError,
)

BOL = {
    "document_type": "BILL_OF_LADING",
    "bol_number": "RGV-1",
    "issued_at": "2026-10-14",
    "line_items": [
        {
            "description": "Aguacate",
            "package_count": 10,
            "package_type": "CARTON",
            "weight": {"value": "1,000", "unit": "KGS"},
        }
    ],
    "total_package_count": 10,
}


def _message(model: str, payload: dict[str, Any] | None, stop_reason: str = "tool_use") -> Message:
    content: list[dict[str, Any]] = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    if payload is not None:
        content.append(
            {"type": "tool_use", "id": "toolu_1", "name": "ExtractedDocument", "input": payload}
        )
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 1200, "output_tokens": 300},
            "content": content,
        }
    )


class FakeMantle:
    """Mimics ``AsyncAnthropicBedrockMantle.messages.create``."""

    def __init__(self, responses: list[dict[str, Any] | None]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    async def create(self, **kwargs: Any) -> Message:
        self.calls.append(kwargs)
        return _message(
            kwargs["model"], self.responses[min(len(self.calls), len(self.responses)) - 1]
        )


@pytest.fixture
def masker() -> PIIMasker:
    return PIIMasker(PII_KEY.encode(), None)


def _service(client: FakeMantle, **overrides: Any) -> BedrockExtractionService:
    return BedrockExtractionService(Settings(environment="test", **overrides), client=client)


def test_extracts_and_normalises(masker: PIIMasker) -> None:
    client = FakeMantle([{"document": BOL}])
    result = anyio.run(
        lambda: _service(client).extract(
            content=b"%PDF", media_type="application/pdf", masker=masker
        )
    )
    assert result.document.document_type == "BILL_OF_LADING"
    assert result.document.gross_weight_lb() is not None
    assert str(result.document.gross_weight_lb()) == "2204.62"
    assert result.model_id == "anthropic.claude-sonnet-5-5"
    assert (result.input_tokens, result.output_tokens) == (1200, 300)

    call = client.calls[0]
    assert call["model"] == "anthropic.claude-sonnet-5-5"
    assert call["tool_choice"] == {
        "type": "auto"
    }  # forced tool_choice is rejected by current models
    assert call["tools"][0]["name"] == "ExtractedDocument"
    assert call["messages"][0]["content"][0]["type"] == "document"


def test_summation_error_is_reasked(masker: PIIMasker) -> None:
    wrong = {"document": {**BOL, "total_package_count": 12}}
    client = FakeMantle([wrong, {"document": BOL}])
    result = anyio.run(
        lambda: _service(client).extract(content=b"x", media_type="image/png", masker=masker)
    )
    assert result.document.effective_package_count == 10
    assert len(client.calls) == 2
    feedback = str(client.calls[1]["messages"][-1])
    assert "line package counts sum to 10 but total is 12" in feedback


def test_gives_up_after_retries(masker: PIIMasker) -> None:
    wrong = {"document": {**BOL, "total_package_count": 12}}
    client = FakeMantle([wrong])
    with pytest.raises(ExtractionError, match="no valid extraction"):
        anyio.run(
            lambda: _service(client, extraction_max_retries=1).extract(
                content=b"x", media_type="image/png", masker=masker
            )
        )


def test_text_input_is_pii_masked_before_sending(masker: PIIMasker) -> None:
    client = FakeMantle([{"document": BOL}])
    text = b"BOL RGV-1 operador CURP GOMJ800101HTSMRN09 licencia BCN0012345"
    anyio.run(
        lambda: _service(client).extract(content=text, media_type="text/plain", masker=masker)
    )
    sent = str(client.calls[0]["messages"])
    assert "GOMJ800101HTSMRN09" not in sent and "BCN0012345" not in sent


def test_unsupported_media_type(masker: PIIMasker) -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        anyio.run(
            lambda: _service(FakeMantle([])).extract(
                content=b"MZ", media_type="application/x-msdownload", masker=masker
            )
        )


class _BedrockControl:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def get_model_invocation_logging_configuration(self) -> dict[str, Any]:
        return self.config


def test_zero_retention_guard() -> None:
    service = _service(FakeMantle([]))
    anyio.run(service.assert_zero_retention, _BedrockControl({}))
    logging_on = {
        "loggingConfig": {
            "s3Config": {"bucketName": "logs"},
            "textDataDeliveryEnabled": True,
        }
    }
    with pytest.raises(RuntimeError, match="invocation logging"):
        anyio.run(service.assert_zero_retention, _BedrockControl(logging_on))
