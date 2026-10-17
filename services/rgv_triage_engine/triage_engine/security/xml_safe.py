"""Hardened XML parsing.

The standard library's ``xml.etree`` will happily expand entity definitions
(billion-laughs) and older stacks resolve external entities (XXE), which can
read local files such as ``/proc/self/environ`` or make SSRF requests to the
instance metadata service. All untrusted XML in this service goes through
:func:`parse_untrusted_xml`, which uses ``defusedxml`` with DTDs, entity
declarations and external references all forbidden.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

#: CFDI files are rarely above a few hundred KB; 5 MiB is generous.
MAX_XML_BYTES = 5 * 1024 * 1024


class UnsafeXMLError(ValueError):
    """The document is malformed, oversized, or uses forbidden XML features."""


def parse_untrusted_xml(data: bytes, *, max_bytes: int = MAX_XML_BYTES) -> Element:
    """Parse untrusted XML bytes with every dangerous feature disabled.

    Args:
        data: Raw XML bytes.
        max_bytes: Upper bound on the document size.

    Returns:
        The root element.

    Raises:
        UnsafeXMLError: On oversize input, any DTD, entity declaration or
            external reference, or malformed XML.
    """
    if len(data) > max_bytes:
        raise UnsafeXMLError(f"XML document exceeds {max_bytes} bytes")
    if not data.strip():
        raise UnsafeXMLError("XML document is empty")
    try:
        root: Element = fromstring(
            data,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except DefusedXmlException as exc:
        raise UnsafeXMLError(f"forbidden XML construct: {type(exc).__name__}") from exc
    except ParseError as exc:
        raise UnsafeXMLError("malformed XML") from exc
    return root
