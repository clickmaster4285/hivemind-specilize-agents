"""Check the commands in a grounded answer against its sources — no model call, a few milliseconds.

The 9B model sometimes "remembers" a command that the sources never mention (B6, 2026-10-19:
`Set-DnsServerConditionalForwarderZone` offered for creating a forwarder). Wrong commands are the most
harmful mistake — people copy and run them — so every command-like term in the answer is looked up in
the passages it was given. Terms that are not there are listed under the answer as "check before use".

Checked:  PowerShell cmdlets (Verb-Noun with an approved verb), anywhere in the answer
          PowerShell parameters (-ZoneName) and dotted calls (logging.basicConfig(...)) inside code
Not checked: plain words, placeholders (<ZoneName>), the user's own variable names.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# the common approved PowerShell verbs (Get-Verb); a capitalised "Word-Word" with another first part
# (e.g. "Hands-On", "Split-Brain") is ordinary English, not a cmdlet
PS_VERBS = (
    "Add", "Clear", "Close", "Copy", "Enter", "Exit", "Find", "Format", "Get", "Hide", "Join", "Lock", "Move",
    "New", "Open", "Optimize", "Pop", "Push", "Redo", "Remove", "Rename", "Reset", "Resize", "Search", "Select",
    "Set", "Show", "Skip", "Step", "Switch", "Undo", "Unlock", "Watch", "Connect", "Disconnect", "Read",
    "Receive", "Send", "Write", "Backup", "Checkpoint", "Compare", "Compress", "Convert", "ConvertFrom",
    "ConvertTo", "Dismount", "Edit", "Expand", "Export", "Group", "Import", "Initialize", "Limit", "Merge",
    "Mount", "Out", "Publish", "Restore", "Save", "Sync", "Unpublish", "Update", "Approve", "Assert", "Build",
    "Complete", "Confirm", "Deny", "Deploy", "Disable", "Enable", "Install", "Invoke", "Register", "Request",
    "Restart", "Resume", "Start", "Stop", "Submit", "Suspend", "Uninstall", "Unregister", "Wait", "Debug",
    "Measure", "Ping", "Repair", "Resolve", "Test", "Trace", "Block", "Grant", "Protect", "Revoke", "Unblock",
    "Unprotect", "Use",
)
CMDLET_RE = re.compile(r"\b(?:%s)-[A-Z][A-Za-z0-9]+\b" % "|".join(PS_VERBS))
CODE_RE = re.compile(r"```[^\n]*\n(.*?)```|`([^`\n]+)`", re.S)
PARAM_RE = re.compile(r"(?<![\w-])-[A-Z][A-Za-z0-9]{2,}\b")
DOTTED_CALL_RE = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?=\()")
# methods every object has: not worth flagging when a source happens not to show them
COMMON_METHODS = frozenset({
    "append", "close", "copy", "decode", "encode", "extend", "format", "get", "items", "join", "keys", "lower",
    "open", "pop", "read", "readline", "replace", "sort", "split", "strip", "update", "upper", "values", "write",
})
NOTE_PREFIX = "> ⚠️ **Check before use:**"


def code_parts(text: str) -> list[str]:
    return [block or span for block, span in CODE_RE.findall(text)]


def command_terms(text: str) -> list[str]:
    """Command-like terms in an answer, in order of first appearance."""
    found: dict[str, None] = {}
    for term in CMDLET_RE.findall(text):
        found.setdefault(term, None)
    for code in code_parts(text):
        for term in PARAM_RE.findall(code):
            found.setdefault(term, None)
        for call in DOTTED_CALL_RE.findall(code):
            if call.rsplit(".", 1)[-1] not in COMMON_METHODS:
                found.setdefault(call, None)
    return list(found)


def _is_in(term: str, haystack: str) -> bool:
    if term.lower() in haystack:
        return True
    # logging.handlers.RotatingFileHandler( is backed by a source that shows RotatingFileHandler
    return "." in term and term.rsplit(".", 1)[-1].lower() in haystack


def unverified_terms(answer: str, source_texts: Iterable[str]) -> list[str]:
    """Command-like terms in the answer that none of the source texts contains (case-insensitive)."""
    haystack = "\n".join(source_texts).lower()
    return [t for t in command_terms(answer) if not _is_in(t, haystack)]


def check_note(terms: list[str]) -> str:
    """The line shown under the answer, or "" when everything was found."""
    if not terms:
        return ""
    listed = ", ".join(f"`{t}`" for t in terms)
    return f"{NOTE_PREFIX} {listed} — not found in the knowledge-base sources, so it may be wrong. Verify before running it."
