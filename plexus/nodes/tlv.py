"""
Reading BER-TLV, and naming what comes out of it.

Shared rather than duplicated: the probe walks TLV to find the AFL and the
records it points at, and the trace tap walks the same bytes to write down what
crossed. One walker, one table.

Pure stdlib, like the rest of the model half — a machine with no reader can
still read a trace.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Tuple


def tlv_walk(data: bytes) -> Iterator[Tuple[int, bytes]]:
    """
    Yield (tag, value) for every TLV in `data`, descending into constructed
    ones.

    Deliberately forgiving: a probe reads whatever a card chose to send, and
    bailing out on the first surprising byte would lose the records before it.
    Anything that does not parse is skipped rather than raised.
    """
    i = 0
    end = len(data)
    while i < end:
        first = data[i]
        if first in (0x00, 0xFF):  # padding between records
            i += 1
            continue

        tag = first
        i += 1
        if first & 0x1F == 0x1F:  # multi-byte tag
            while i < end:
                tag = (tag << 8) | data[i]
                more = data[i] & 0x80
                i += 1
                if not more:
                    break

        if i >= end:
            return
        length = data[i]
        i += 1
        if length & 0x80:
            count = length & 0x7F
            if count == 0 or i + count > end:
                return
            length = int.from_bytes(data[i : i + count], "big")
            i += count

        if i + length > end:
            return
        value = data[i : i + length]
        i += length

        yield tag, value
        if first & 0x20:  # constructed
            yield from tlv_walk(value)


def find_all(data: bytes, tag: int) -> List[bytes]:
    return [value for found, value in tlv_walk(data) if found == tag]


#: EMV tags, by the names EMV Book 3 Annex A gives them.
#:
#: Not exhaustive on purpose — this is the set a relay actually turns up, and a
#: half-remembered name is worse than none. A tag that is not here is written
#: as its hex, which is what the whole table used to be.
TAG_NAMES: Dict[int, str] = {
    0x4F: "Application Identifier (AID)",
    0x50: "Application Label",
    0x57: "Track 2 Equivalent Data",
    0x5A: "Application PAN",
    0x61: "Application Template",
    0x6F: "FCI Template",
    0x70: "Record Template",
    0x77: "Response Template (format 2)",
    0x80: "Response Template (format 1)",
    0x82: "Application Interchange Profile",
    0x84: "Dedicated File Name",
    0x87: "Application Priority Indicator",
    0x88: "Short File Identifier",
    0x8C: "CDOL1",
    0x8D: "CDOL2",
    0x8E: "CVM List",
    0x8F: "CA Public Key Index",
    0x90: "Issuer Public Key Certificate",
    0x94: "Application File Locator (AFL)",
    0x95: "Terminal Verification Results",
    0x9A: "Transaction Date",
    0x9C: "Transaction Type",
    0xA5: "FCI Proprietary Template",
    0x5F20: "Cardholder Name",
    0x5F24: "Application Expiration Date",
    0x5F25: "Application Effective Date",
    0x5F28: "Issuer Country Code",
    0x5F2A: "Transaction Currency Code",
    0x5F2D: "Language Preference",
    0x5F34: "Application PAN Sequence Number",
    0x9F02: "Amount, Authorised",
    0x9F03: "Amount, Other",
    0x9F06: "Application Identifier (terminal)",
    0x9F07: "Application Usage Control",
    0x9F08: "Application Version Number",
    0x9F0D: "Issuer Action Code - Default",
    0x9F0E: "Issuer Action Code - Denial",
    0x9F0F: "Issuer Action Code - Online",
    0x9F10: "Issuer Application Data",
    0x9F1A: "Terminal Country Code",
    0x9F26: "Application Cryptogram",
    0x9F27: "Cryptogram Information Data",
    0x9F36: "Application Transaction Counter",
    0x9F37: "Unpredictable Number",
    0x9F38: "PDOL",
    0x9F42: "Application Currency Code",
    0x9F4A: "SDA Tag List",
    0x9F66: "Terminal Transaction Qualifiers (TTQ)",
    0xBF0C: "FCI Issuer Discretionary Data",
}

#: Status words worth spelling out. The second byte varies for some of these,
#: so `status_word` falls back to the SW1 entry when the pair is not listed.
STATUS_WORDS: Dict[int, str] = {
    0x9000: "OK",
    0x6283: "selected file invalidated",
    0x6300: "authentication failed",
    0x6700: "wrong length",
    0x6982: "security status not satisfied",
    0x6985: "conditions of use not satisfied",
    0x6A81: "function not supported",
    0x6A82: "file or application not found",
    0x6A83: "record not found",
    0x6A86: "incorrect P1 P2",
    0x6A88: "referenced data not found",
    0x6D00: "instruction not supported",
    0x6E00: "class not supported",
}

_STATUS_BY_SW1: Dict[int, str] = {
    0x61: "response bytes still available",
    0x62: "warning, state of non-volatile memory unchanged",
    0x63: "warning, state of non-volatile memory changed",
    0x64: "error, state of non-volatile memory unchanged",
    0x65: "error, state of non-volatile memory changed",
    0x6C: "wrong Le",
}


def tag_name(tag: int) -> Optional[str]:
    """What a tag is called, or nothing when this table does not know."""
    return TAG_NAMES.get(tag)


def tag_hex(tag: int) -> str:
    """A tag as it is written: two hex digits per byte, however many bytes."""
    width = 2
    while tag >= 1 << (width * 4):
        width += 2
    return "%0*X" % (width, tag)


def status_word(response: bytes) -> Optional[Dict[str, str]]:
    """
    The trailing SW1 SW2 of a response, and what it means.

    None when there is no room for one. The meaning may be absent while the
    hex is not: an unlisted status word is still worth writing down.
    """
    if len(response) < 2:
        return None
    sw = int.from_bytes(response[-2:], "big")
    out = {"sw": "%04X" % sw}
    meaning = STATUS_WORDS.get(sw) or _STATUS_BY_SW1.get(response[-2])
    if meaning:
        out["meaning"] = meaning
    return out


def decode(data: bytes, *, named: bool = False) -> List[Dict[str, Any]]:
    """
    Every TLV in `data`, flattened, in the order it appears.

    `named` adds the tag's name where this build knows one. Constructed tags
    appear before the tags inside them, which is the order `tlv_walk` yields
    and the order a person reads.
    """
    out: List[Dict[str, Any]] = []
    for tag, value in tlv_walk(data):
        entry: Dict[str, Any] = {"tag": tag_hex(tag), "value": value.hex().upper()}
        if named:
            name = tag_name(tag)
            if name:
                entry["name"] = name
        out.append(entry)
    return out
