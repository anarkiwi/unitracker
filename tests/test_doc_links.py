"""tools/doc_links.py: every relative link in README.md and docs/ resolves."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("doc_links", ROOT / "tools" / "doc_links.py")
doc_links = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(doc_links)


def test_the_repository_docs_have_no_broken_links():
    assert not doc_links.broken(ROOT)


def test_a_missing_target_is_reported_and_external_links_are_not(tmp_path, capsys):
    (tmp_path / "docs" / "sub").mkdir(parents=True)
    (tmp_path / "README.md").write_text(
        "# Top\n[a](docs/a.md) [b](https://x.org) [c](#top) [d](mailto:a@b)\n"
    )
    (tmp_path / "docs" / "a.md").write_text("[up](../README.md#top) [dir](sub/)\n[gone](b.md)\n")
    assert doc_links.broken(tmp_path) == ["docs/a.md:2: b.md"]
    assert doc_links.main([str(tmp_path)]) == 1
    assert capsys.readouterr().out == "docs/a.md:2: b.md\n"
    (tmp_path / "docs" / "b.md").write_text("")
    assert doc_links.main([str(tmp_path)]) == 0


def test_an_anchor_must_be_a_heading_of_its_target(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text(
        "# Top\n## A `b`, c\n## A `b`, c\n```\n# not a heading\n[x](#nope)\n```\n"
        "[ok](#a-b-c-1) [ok](docs/d.md#the-cursor) [bad](docs/d.md#not-a-heading) [bad](#e)\n"
    )
    (tmp_path / "docs" / "d.md").write_text("# D\n### The cursor\n```\n## Not a heading\n```\n")
    assert doc_links.broken(tmp_path) == ["README.md:8: docs/d.md#not-a-heading", "README.md:8: #e"]
    assert doc_links.slug("Ticks, the ghost and the `flush`") == "ticks-the-ghost-and-the-flush"
