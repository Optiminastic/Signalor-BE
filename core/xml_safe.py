"""XML parsing hardened against entity-expansion denial of service.

Every XML this backend parses is remote and attacker-controlled: sitemaps come
from whatever domain a user asks us to analyse, and Search Console responses
come over the wire. ``xml.etree.ElementTree`` blocks *external* entities on
this Python (an XXE ``file://`` read raises "undefined entity"), but it still
expands **internal** ones, which is the billion-laughs amplification:

    <!DOCTYPE r [<!ENTITY a "AAAAAAAAAA"><!ENTITY b "&a;&a;...">]><r>&c;</r>

A few hundred bytes of sitemap becomes gigabytes in memory. The analysis
workers are shared, so one hostile domain submitted for analysis can OOM the
pipeline for every customer.

The fix is to refuse documents that declare a DTD at all. None of the formats
we consume (sitemaps, sitemap indexes, GSC payloads) legitimately carry one, so
this costs nothing and removes the mechanism rather than trying to bound it.

Why not a byte-scan for "<!DOCTYPE": the document's encoding is whatever the
remote server chose, so a UTF-16 payload would slip straight past an ASCII
substring check. Expat decodes properly, so we let expat make the ruling.

Why not defusedxml: it is the canonical answer, but this needs ~30 lines of
stdlib and the repo prefers standard libraries over new dependencies. If more
XML surfaces appear, switching to defusedxml is the right call.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.parsers import expat


class UnsafeXMLError(ValueError):
    """The document declared a DTD, which we refuse to expand."""


class _ProlgueScanned(Exception):  # noqa: N818 - control flow, never surfaces
    """Raised to stop expat once the prologue is behind us."""


def _reject_dtd(data: bytes | str) -> None:
    """Raise ``UnsafeXMLError`` if the document declares a doctype or entity.

    Only the prologue is read: a DTD must appear before the root element, so we
    abort at the first start tag rather than parsing the whole payload twice.
    """
    parser = expat.ParserCreate()

    def on_doctype(*_args, **_kwargs):
        raise UnsafeXMLError("XML document declares a DTD")

    def on_entity(*_args, **_kwargs):
        raise UnsafeXMLError("XML document declares an entity")

    def on_start(*_args, **_kwargs):
        raise _ProlgueScanned

    parser.StartDoctypeDeclHandler = on_doctype
    parser.EntityDeclHandler = on_entity
    parser.StartElementHandler = on_start

    payload = data.encode("utf-8", errors="replace") if isinstance(data, str) else data
    try:
        parser.Parse(payload, True)
    except _ProlgueScanned:
        return
    except expat.ExpatError:
        # Malformed XML. Let the real parser produce the error the callers
        # already handle (ET.ParseError), so behaviour here stays unchanged.
        return


def fromstring(data: bytes | str) -> ET.Element:
    """``ET.fromstring`` that refuses DTD-bearing documents.

    Raises ``UnsafeXMLError`` for a DTD and ``ET.ParseError`` for malformed
    input, so existing ``except ET.ParseError`` handlers keep working.
    """
    _reject_dtd(data)
    return ET.fromstring(data)
