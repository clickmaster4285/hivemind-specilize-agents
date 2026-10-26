"""Safety classifier for PowerShell commands run on a Windows server over SSH (Phase 1: operate/diagnose).

The Windows counterpart of the bash classifier in src/server_ops.py. PowerShell cmdlets are Verb-Noun,
so the rule is verb-based: a command is READ-ONLY only when every segment is a known read verb (or a known
read-only native tool); anything else is treated as a CHANGE (gated by the server's mode), and a few
catastrophes are blocked outright. Conservative by design — anything not understood is treated as a change
(so it needs the user's confirmation), never silently run as read-only.

Two entry points mirror the Linux ones:
  classify_windows_command(cmd) -> str | None   # reason it is NOT read-only, or None if it is
  is_destructive_windows(cmd)   -> str | None   # reason it must never run (even trusted), or None
"""
from __future__ import annotations

import base64
import re


def ps_wrap(command: str) -> str:
    """Wrap a PowerShell command to run over SSH without any quoting problems: -EncodedCommand takes
    base64 of the UTF-16LE text, so quotes, newlines and special characters pass through untouched."""
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"

# Verbs whose cmdlets only read / display / transform data (Get-Process, Test-Path, Select-Object, …).
READ_VERBS: frozenset[str] = frozenset({
    "get", "test", "measure", "select", "compare", "resolve", "find", "search",
    "sort", "group", "convertfrom", "convertto", "where", "join", "split", "trace",
})
# Verbs that change the machine. Listed for a clear message; an unknown verb is treated as a change anyway.
WRITE_VERBS: frozenset[str] = frozenset({
    "set", "new", "remove", "start", "stop", "restart", "rename", "copy", "move", "add", "install",
    "uninstall", "enable", "disable", "clear", "reset", "update", "write", "export", "import", "invoke",
    "register", "unregister", "suspend", "resume", "push", "pop", "send", "connect", "disconnect",
    "mount", "dismount", "block", "unblock", "protect", "unprotect", "grant", "revoke", "lock", "unlock",
    "save", "checkpoint", "restore", "repair", "optimize", "compress", "expand", "format", "out",
})
# Verbs that are mostly writers but have a few read-only nouns — allow only those nouns.
AMBIGUOUS: dict[str, frozenset[str]] = {
    "out": frozenset({"string", "host", "default", "null"}),      # Out-File / Out-Printer write
    "format": frozenset({"table", "list", "wide", "custom", "hex"}),  # Format-Volume wipes a disk
}
# Native (non-cmdlet) tools that only read.
NATIVE_READ: frozenset[str] = frozenset({
    "ipconfig", "systeminfo", "tasklist", "netstat", "nslookup", "ping", "tracert", "pathping",
    "whoami", "hostname", "getmac", "arp", "ver", "dir", "type", "findstr", "gpresult", "driverquery",
    "qwinsta", "cmdkey", "klist", "vol", "chcp", "echo", "where",
})

_SEGMENTS = re.compile(r"\|\||&&|\||;|\n|`n")          # PS statement / pipeline separators
_CMDLET = re.compile(r"^([A-Za-z]+)-([A-Za-z]+)")      # Verb-Noun
_ASSIGN = re.compile(r"^\s*\$[\w:]+\s*=\s*")           # $var = <command>
_REDIRECT = re.compile(r"\d*>>?")                      # > or >> (PS comparison is -gt/-lt, so > is a redirect)


def _check_segment(segment: str) -> str | None:
    """A single PowerShell segment (no pipe / ;): reason it is NOT read-only, or None if it is."""
    seg = segment.strip()
    if not seg:
        return None
    seg = _ASSIGN.sub("", seg).strip()                 # "$x = Get-Process" → classify the right-hand side
    if not seg:
        return None
    if seg[0] in "&.":                                 # call operator: &"c:\x.exe" / . .\script.ps1
        return "uses the call operator (& or .) to run a program — not a read-only command"
    if _REDIRECT.search(seg):
        return "it writes to a file (>)"
    first = seg.split()[0].strip('"\'')
    m = _CMDLET.match(first)
    if m:
        verb, noun = m.group(1).lower(), m.group(2).lower()
        if verb in AMBIGUOUS:
            return None if noun in AMBIGUOUS[verb] else f"'{first}' changes the server"
        if verb in READ_VERBS:
            return None
        if verb in WRITE_VERBS:
            return f"'{first}' changes the server"
        return f"'{first}' is not a known read-only command"
    prog = first.lower().rsplit("\\", 1)[-1]
    if prog.endswith(".exe"):
        prog = prog[:-4]
    if prog in NATIVE_READ:
        return None
    return f"'{first}' is not a read-only command"


def classify_windows_command(command: str) -> str | None:
    """Reason the PowerShell command is NOT read-only, or None when every part only reads. Conservative:
    anything not understood is treated as a change (needs confirmation), never run as read-only."""
    command = (command or "").strip()
    if not command:
        return "empty command"
    if len(command) > 2000:
        return "the command is too long"
    for segment in _SEGMENTS.split(command):
        reason = _check_segment(segment)
        if reason:
            return reason
    return None


# Catastrophes refused even in "trusted" mode and even if the user clicks Run — the floor under the
# confirm box, like the Linux DENY list. Arbitrary-code / download-and-run escapes are here because they
# would bypass the verb check entirely.
DENY: list[tuple[re.Pattern, str | None]] = [
    (re.compile(r"\b(format-volume|clear-disk|remove-partition|initialize-disk)\b", re.I), "formats or wipes a disk"),
    (re.compile(r"\bdiskpart\b", re.I), "runs diskpart (can wipe disks)"),
    (re.compile(r"\bformat(\.com|\.exe)?\s+[a-z]:", re.I), "formats a drive"),
    (re.compile(r"\b(stop-computer|restart-computer)\b", re.I), "shuts down or reboots the server"),
    (re.compile(r"\bshutdown(\.exe)?\b[^;|]*\s/(r|s|p|g)\b", re.I), "shuts down or reboots the server"),
    (re.compile(r"\b(invoke-expression|iex)\b", re.I), "runs arbitrary code (Invoke-Expression)"),
    (re.compile(r"\b(downloadstring|downloadfile|start-bitstransfer)\b", re.I), "downloads and could run remote code"),
    (re.compile(r"\badd-type\b", re.I), "compiles and loads arbitrary code"),
    (re.compile(r"\bcipher\b[^;|]*\s/w", re.I), "wipes free disk space"),
    (re.compile(r"\b(bcdedit|bootrec)\b", re.I), "changes boot configuration"),
    (re.compile(r"\breg(\.exe)?\s+delete\s+HK(LM|EY_LOCAL_MACHINE)\\(SYSTEM|SOFTWARE)\b", re.I),
     "deletes critical registry keys"),
    (re.compile(r"\b(rd|rmdir)\s+/s\b[^;|]*\s[a-z]:\\?(\s|$|windows|users)", re.I),
     "recursively deletes a system path"),
    (re.compile(r"\bdel\s+/[sfq]\b[^;|]*\s[a-z]:\\", re.I), "recursively deletes a system path"),
    # Remove-Item with -Recurse and -Force: refused only when it targets a drive root / system path (below).
    (re.compile(r"\bremove-item\b(?=[^;|]*\s-recurse\b)(?=[^;|]*\s-force\b)", re.I), None),
]
# A Remove-Item -Recurse -Force aimed at a drive root, C:\Windows, C:\Users or a wildcard at root.
_REMOVE_ROOT = re.compile(
    r"\bremove-item\b[^;|]*\s(?:-path\s+)?['\"]?([a-z]:\\?(\\\*)?(\s|$|'|\"|windows\b|users\b)|[a-z]:\\\*|\\\\)",
    re.I,
)


def is_destructive_windows(command: str) -> str | None:
    """Reason a PowerShell command must never run (even trusted / even confirmed), or None."""
    command = command or ""
    for pattern, reason in DENY:
        if pattern.search(command):
            if reason is None:                         # the Remove-Item -Recurse -Force rule
                if _REMOVE_ROOT.search(command):
                    return "a recursive, forced delete of a drive root or system path"
                continue
            return reason
    return None
