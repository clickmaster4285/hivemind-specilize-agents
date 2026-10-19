"""B2 unit tests (no database, no Ollama): extract, chunking, source registry, page URLs."""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.brain.chunking import MAX_TOKENS, chunk_markdown, estimate_tokens
from src.brain.extract import extract_markdown
from src.brain.fetch import page_url_for
from src.brain.sources import RegistryError, load_registry, parse_interval

MS_DOC = """---
title: Domain Name System (DNS) in Windows Server
description: Discover the role of DNS.
ms.topic: overview
ms.date: 05/15/2025
---

# What is Domain Name System (DNS)?

DNS maps computer names to IP addresses. See [the quickstart](quickstart.md) for setup.

:::image type="content" source="../media/dns.jpg" alt-text="Diagram of DNS in the TCP/IP model.":::

[!INCLUDE [some include](../includes/x.md)]

> [!NOTE]
> Restart the DNS Server service after changing forwarders.

::: zone pivot="windows-server-2025"
Zone-specific text stays.
::: zone-end

```powershell
# not a heading: a PowerShell comment
Add-DnsServerForwarder -IPAddress 10.0.0.1
```
"""


def test_extract_reads_front_matter_and_cleans_ms_markup():
    doc = extract_markdown(MS_DOC, fallback_title="fallback")
    assert doc.title == "Domain Name System (DNS) in Windows Server"
    assert doc.description == "Discover the role of DNS."
    assert doc.updated_at is not None and doc.updated_at.isoformat().startswith("2025-05-15")
    md = doc.content_md
    assert "See the quickstart for setup." in md                      # link text kept, target dropped
    assert "*(Image: Diagram of DNS in the TCP/IP model.)*" in md      # image -> alt text
    assert "[!INCLUDE" not in md and ":::" not in md                    # publishing markup removed
    assert "> **Note:**" in md
    assert "Zone-specific text stays." in md
    assert "# not a heading: a PowerShell comment" in md               # code block untouched


def test_extract_adds_title_heading_when_missing():
    doc = extract_markdown("Just text.\n", fallback_title="My page")
    assert doc.title == "My page"
    assert doc.content_md.startswith("# My page")


def _section(title: str, paragraphs: int, words: int = 60) -> str:
    para = " ".join(["word"] * words) + "."
    return f"## {title}\n\n" + "\n\n".join(para for _ in range(paragraphs)) + "\n\n"


def test_chunks_follow_headings_and_stay_under_the_limit():
    md = "# Guide\n\nIntro " + "text " * 80 + "\n\n" + _section("Install", 12) + _section("Configure", 3)
    chunks = chunk_markdown(md)
    assert len(chunks) > 2
    assert all(c.token_count <= MAX_TOKENS for c in chunks)
    assert any(c.heading_path == "Guide > Install" for c in chunks)
    assert any(c.heading_path == "Guide > Configure" for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_neighbouring_chunks_overlap():
    # realistic paragraphs (~120 tokens, several sentences) are longer than the overlap budget
    paragraphs = [
        " ".join(f"Paragraph {i} sentence {j} explains one detail of the setup." for j in range(8))
        for i in range(12)
    ]
    md = "# Doc\n\n## Long section\n\n" + "\n\n".join(paragraphs)
    chunks = chunk_markdown(md)
    assert len(chunks) >= 2
    for before, after in zip(chunks, chunks[1:]):
        last_sentence = before.content.rsplit(". ", 1)[-1]
        assert last_sentence in after.content  # the boundary is repeated in the next chunk
        assert after.token_count <= MAX_TOKENS


def test_code_blocks_are_never_split():
    code = "```bash\n" + "\n".join(f"echo line {i}" for i in range(400)) + "\n\n# comment after blank line\n```"
    md = f"# Doc\n\n## Script\n\nRun this:\n\n{code}\n\nDone."
    chunks = chunk_markdown(md)
    holders = [c for c in chunks if "echo line 0" in c.content]
    assert len(holders) == 1
    assert "echo line 399" in holders[0].content and "# comment after blank line" in holders[0].content
    assert all(c.heading_path != "Doc > comment after blank line" for c in chunks)


def test_tiny_sections_are_merged():
    md = "# Doc\n\n## A\n\nShort.\n\n## B\n\n" + "Longer text here. " * 40
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert "Short." in chunks[0].content and "## B" in chunks[0].content


def test_empty_document_gives_no_chunks():
    assert chunk_markdown("") == []


def test_estimate_tokens():
    assert estimate_tokens("abcd" * 100) == 100


def test_page_url_uses_published_page():
    cfg = {"page_url": "https://learn.microsoft.com/en-us/windows-server/{path}", "page_root": "WindowsServerDocs"}
    assert (
        page_url_for("WindowsServerDocs/networking/dns/zone-types.md", "https://github.com/x/y", cfg)
        == "https://learn.microsoft.com/en-us/windows-server/networking/dns/zone-types"
    )
    assert page_url_for("WindowsServerDocs/networking/dns/index.md", None, cfg).endswith("/networking/dns")
    assert page_url_for("docs/a.md", "https://github.com/x/y", {}) == "https://github.com/x/y/blob/HEAD/docs/a.md"
    assert page_url_for("a.md", None, {}) == "file:a.md"


def _registry(tmp_path, body: str):
    path = tmp_path / "sources.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_registry_loads_valid_entry(tmp_path):
    specs = load_registry(_registry(tmp_path, """
sources:
  - slug: my-docs
    title: My docs
    kind: git_repo
    url: https://github.com/x/y
    collection: it
    licence: CC-BY-4.0
    refresh_every: 12h
    max_pages: 10
    page_root: docs
    enabled: true
"""))
    assert len(specs) == 1
    assert specs[0].refresh_every == timedelta(hours=12)
    assert specs[0].config == {"page_root": "docs"}


@pytest.mark.parametrize(
    "entry, message",
    [
        ("slug: a-b\n    title: t\n    kind: git_repo\n    url: u\n    collection: it\n    enabled: true", "licence"),
        ("slug: Bad Slug\n    title: t\n    kind: upload\n    path: x\n    collection: it", "slug"),
        ("slug: a-b\n    title: t\n    kind: ftp\n    collection: it", "kind"),
        ("slug: a-b\n    title: t\n    kind: git_repo\n    collection: it", "url"),
    ],
)
def test_registry_rejects_invalid_entries(tmp_path, entry, message):
    with pytest.raises(RegistryError, match=message):
        load_registry(_registry(tmp_path, f"sources:\n  - {entry}\n"))


def test_registry_rejects_duplicate_slugs(tmp_path):
    one = "  - slug: dup\n    title: t\n    kind: upload\n    path: x\n    collection: it\n"
    with pytest.raises(RegistryError, match="duplicate"):
        load_registry(_registry(tmp_path, "sources:\n" + one + one))


def test_parse_interval():
    assert parse_interval("7d") == timedelta(days=7)
    assert parse_interval("30m") == timedelta(minutes=30)
    with pytest.raises(RegistryError):
        parse_interval("weekly")


def test_real_registry_is_valid():
    specs = load_registry()
    assert any(s.slug == "ms-windows-server-dns" and s.enabled for s in specs)
