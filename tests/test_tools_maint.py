"""tools/maint.py: the docs' links, the playroutine docs' tables and lost coverage lines."""

import json

import maint
import shared


def test_the_repository_docs_have_no_broken_links_and_tables_match(capsys):
    assert not maint.broken(shared.ROOT)
    assert maint.main(["tables"]) == 0
    assert "| `!" in capsys.readouterr().out


def test_a_missing_target_is_reported_and_external_links_are_not(tmp_path, capsys):
    (tmp_path / "docs" / "sub").mkdir(parents=True)
    (tmp_path / "README.md").write_text(
        "# Top\n[a](docs/a.md) [b](https://x.org) [c](#top) [d](mailto:a@b)\n"
    )
    (tmp_path / "docs" / "a.md").write_text("[up](../README.md#top) [dir](sub/)\n[gone](b.md)\n")
    assert maint.broken(tmp_path) == ["docs/a.md:2: b.md"]
    assert maint.main(["links", str(tmp_path)]) == 1
    assert capsys.readouterr().out == "docs/a.md:2: b.md\n"
    (tmp_path / "docs" / "b.md").write_text("")
    assert maint.main(["links", str(tmp_path)]) == 0


def test_an_anchor_must_be_a_heading_of_its_target(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text(
        "# Top\n## A `b`, c\n## A `b`, c\n```\n# not a heading\n[x](#nope)\n```\n"
        "[ok](#a-b-c-1) [ok](docs/d.md#the-cursor) [bad](docs/d.md#not-a-heading) [bad](#e)\n"
    )
    (tmp_path / "docs" / "d.md").write_text("# D\n### The cursor\n```\n## Not a heading\n```\n")
    assert maint.broken(tmp_path) == ["README.md:8: docs/d.md#not-a-heading", "README.md:8: #e"]
    assert maint.slug("Ticks, the ghost and the `flush`") == "ticks-the-ghost-and-the-flush"


def test_coverage_lines_one_run_loses_are_listed_as_ranges(tmp_path, capsys):
    def report(name, executed):
        path = tmp_path / name
        files = {"a.py": {"executed_lines": executed, "summary": {"num_statements": 10}}}
        path.write_text(json.dumps({"files": files}))
        return str(path)

    before, after = report("b.json", [1, 2, 3, 5, 7, 8]), report("a.json", [1, 5])
    assert maint.main(["coverage", before, after]) == 1
    assert capsys.readouterr().out == "a.py: 60.0% -> 20.0%, lost 2-3, 7-8\n"
    assert maint.main(["coverage", before, before]) == 0
    assert not maint.lost(before, after, ["b.py"])
