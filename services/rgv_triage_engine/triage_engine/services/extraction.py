"""Structured document extraction with Claude on Amazon Bedrock via ``instructor``.

Data handling
-------------
* All model traffic goes to the Amazon Bedrock Mantle endpoint in the
  configured region (optionally through a VPC interface endpoint), using
  SigV4 credentials from the task role. No first-party Anthropic API key or
  endpoint is involved.
* Amazon Bedrock does not store or log prompts or completions unless
  *model invocation logging* is turned on for the account. The service
  checks at startup that it is off (see :meth:`assert_zero_retention`), so
  documents are processed without retention by the inference layer.
* Text inputs are PII-tokenised before they are sent. Images and PDFs cannot
  be redacted pixel-wise; that is why the zero-retention path is mandatory
  for them, and why extracted identifiers are tokenised before storage.
* The audit trail records model ID, prompt version and SHA-256 digests of
  inputs and outputs, never the content itself.

instructor integration
----------------------
``instructor.from_anthropic`` only accepts the legacy client classes, and its
default ``TOOLS`` mode forces ``tool_choice={"type": "tool"}``, which current
Claude models reject. The client below wraps the Mantle client's
``messages.create`` with instructor's provider patcher directly and sends
``tool_choice={"type": "auto"}`` plus a prompt instruction. Validation
failures (including the arithmetic validators on the schemas) are re-asked
up to ``extraction_max_retries`` times.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

import anthropic
import anyio
from anthropic import AsyncAnthropicBedrockMantle
from instructor.v2.core.client import AsyncInstructor
from instructor.v2.core.errors import InstructorError
from instructor.v2.core.mode import Mode
from instructor.v2.core.patch import patch_v2
from instructor.v2.core.providers import Provider
from instructor.v2.providers.anthropic import handlers as _handlers  # noqa: F401  (registers modes)

from triage_engine.config import Settings
from triage_engine.schemas.documents import (
    BillOfLadenDocument,
    ExtractedDocument,
    MexicanFacturaDocument,
)
from triage_engine.security.pii import PIIMasker

logger = logging.getLogger(__name__)

PROMPT_VERSION = "triage-extract-2026-10-01"

SYSTEM_PROMPT = """\
You extract data from cross-border freight documents moving between Mexico and \
the United States through the Rio Grande Valley ports of entry.

The document is either a Mexican SAT CFDI 4.0 factura (invoice, possibly with a \
Carta Porte complement) or a bill of lading. Record it by calling the \
ExtractedDocument tool exactly once. Do not answer in prose.

Rules:
- Transcribe numbers exactly as printed. Never convert units or compute totals; \
record each weight with the unit printed next to it (KG or LB).
- If a field is not on the document, leave it null. Do not guess.
- "Bultos", "cajas" and "cartons" are handling units; count them as packages.
- Record the number of pallets ("tarimas") separately when printed.
- Some identifiers in text may already be replaced with tokens like \
PII:RFC:0123abcd...; copy tokens verbatim.
"""

PDF_MEDIA_TYPES = frozenset({"application/pdf"})
IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
TEXT_MEDIA_TYPES = frozenset({"text/plain"})


class ExtractionError(Exception):
    """The document could not be extracted into a valid schema."""


class UnsupportedMediaTypeError(ExtractionError):
    """The attachment type is not accepted for extraction."""


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """A validated document plus the provenance needed for the audit trail."""

    document: MexicanFacturaDocument | BillOfLadenDocument
    model_id: str
    prompt_version: str
    input_sha256: str
    output_sha256: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


def build_bedrock_client(settings: Settings) -> AsyncAnthropicBedrockMantle:
    """Create the Bedrock Mantle client (SigV4 auth from the AWS credential chain)."""
    kwargs: dict[str, Any] = {
        "aws_region": settings.aws_region,
        "timeout": settings.bedrock_timeout_seconds,
        "max_retries": 2,
    }
    if settings.bedrock_endpoint_url:
        kwargs["base_url"] = settings.bedrock_endpoint_url
    return AsyncAnthropicBedrockMantle(**kwargs)


def build_instructor(client: Any, model_id: str) -> AsyncInstructor:
    """Wrap an async Anthropic-compatible client's ``messages.create`` with instructor."""
    patched = patch_v2(
        func=client.messages.create,
        provider=Provider.ANTHROPIC,
        mode=Mode.TOOLS,
        default_model=model_id,
    )
    return AsyncInstructor(
        client=client, create=patched, provider=Provider.ANTHROPIC, mode=Mode.TOOLS
    )


def content_block(content: bytes, media_type: str, masker: PIIMasker) -> dict[str, Any]:
    """Build the Messages API content block for one attachment.

    Raises:
        UnsupportedMediaTypeError: For anything but PDF, common images, or text.
    """
    media_type = media_type.split(";", 1)[0].strip().lower()
    if media_type in PDF_MEDIA_TYPES:
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(content).decode("ascii"),
            },
        }
    if media_type in IMAGE_MEDIA_TYPES:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(content).decode("ascii"),
            },
        }
    if media_type in TEXT_MEDIA_TYPES:
        text = masker.mask_text(content.decode("utf-8", errors="replace"))
        return {"type": "text", "text": f"<document>\n{text}\n</document>"}
    raise UnsupportedMediaTypeError(f"unsupported media type {media_type!r}")


class BedrockExtractionService:
    """Extracts factura / BOL schemas from attachments with Claude on Bedrock.

    Args:
        settings: Application settings.
        client: Optional pre-built client (tests inject a fake here).
    """

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._raw_client = client or build_bedrock_client(settings)
        self._instructor = build_instructor(self._raw_client, settings.bedrock_model_id)

    async def assert_zero_retention(self, bedrock_control_client: Any) -> None:
        """Fail if Bedrock model invocation logging would retain prompts/outputs.

        Args:
            bedrock_control_client: A ``boto3`` ``bedrock`` (control plane) client.

        Raises:
            RuntimeError: If any invocation logging destination is enabled.
        """

        def _check() -> None:
            config = bedrock_control_client.get_model_invocation_logging_configuration()
            logging_config = config.get("loggingConfig") or {}
            destinations = [
                key for key in ("cloudWatchConfig", "s3Config") if logging_config.get(key)
            ]
            delivers_data = any(
                logging_config.get(flag)
                for flag in (
                    "textDataDeliveryEnabled",
                    "imageDataDeliveryEnabled",
                    "embeddingDataDeliveryEnabled",
                    "videoDataDeliveryEnabled",
                )
            )
            if destinations and delivers_data:
                raise RuntimeError(
                    "Bedrock model invocation logging is enabled; it would retain "
                    "document content. Disable it for zero-retention processing."
                )

        await anyio.to_thread.run_sync(_check)

    async def extract(
        self, *, content: bytes, media_type: str, masker: PIIMasker
    ) -> ExtractionResult:
        """Extract a factura or BOL from one attachment.

        Args:
            content: Raw attachment bytes.
            media_type: MIME type of the attachment.
            masker: Tenant-scoped PII masker (applied to text input).

        Raises:
            UnsupportedMediaTypeError: Attachment type not accepted.
            ExtractionError: Model call failed, refused, or never produced a
                schema-valid result.
        """
        block = content_block(content, media_type, masker)
        messages: list[Any] = [
            {
                "role": "user",
                "content": [
                    block,
                    {
                        "type": "text",
                        "text": "Extract this document with the ExtractedDocument tool.",
                    },
                ],
            }
        ]
        started = time.monotonic()
        try:
            result, completion = await self._instructor.create_with_completion(
                messages=messages,
                response_model=ExtractedDocument,
                max_retries=self._settings.extraction_max_retries,
                system=SYSTEM_PROMPT,
                max_tokens=self._settings.bedrock_max_tokens,
                tool_choice={"type": "auto"},
                output_config={"effort": self._settings.bedrock_effort},
            )
        except anthropic.RateLimitError as exc:
            raise ExtractionError("Bedrock throttled the request") from exc
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"Bedrock returned HTTP {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("could not reach Bedrock") from exc
        except InstructorError as exc:
            raise ExtractionError(f"no valid extraction: {type(exc).__name__}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        if getattr(completion, "stop_reason", None) == "refusal":
            raise ExtractionError("model declined the request")
        usage = getattr(completion, "usage", None)
        document = result.document
        return ExtractionResult(
            document=document,
            model_id=self._settings.bedrock_model_id,
            prompt_version=PROMPT_VERSION,
            input_sha256=hashlib.sha256(content).hexdigest(),
            output_sha256=hashlib.sha256(document.model_dump_json().encode()).hexdigest(),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            latency_ms=latency_ms,
        )
