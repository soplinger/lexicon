import json
import os

import pytest

import lexicon

GLOSSARY = """\
term: GitHub | Github, github, Git Hub | Code hosting
term: pull request | pull-request, PR | Proposed change
term: allowlist | whitelist | Permitted values
term: sign in | log in, login | Authenticate
ban: master branch | main branch | inclusive terminology
acronym: API | Application Programming Interface
"""


def parsed(glossary_text=GLOSSARY):
    glossary = lexicon.parse_glossary(glossary_text)
    assert glossary.errors == []
    return glossary


def scan(text, glossary_text=GLOSSARY, name="doc.md"):
    glossary = parsed(glossary_text)
    rules = lexicon.build_rules(glossary)
    return lexicon.scan_document(name, text, glossary, rules)


def findings_for(text, glossary_text=GLOSSARY, name="doc.md"):
    return lexicon.to_findings(name, scan(text, glossary_text, name))


def write_glossary(tmp_path, text=GLOSSARY):
    path = tmp_path / "glossary.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_parse_reads_every_entry_kind():
    glossary = parsed()
    assert len(glossary.terms) == 4
    assert len(glossary.bans) == 1
    assert len(glossary.acronyms) == 1
    assert glossary.terms[0].preferred == "GitHub"
    assert glossary.terms[0].aliases == ["Github", "github", "Git Hub"]
    assert glossary.terms[0].definition == "Code hosting"
    assert glossary.bans[0].replacement == "main branch"
    assert glossary.bans[0].reason == "inclusive terminology"
    assert glossary.acronyms[0].expansion == "Application Programming Interface"


def test_parse_reports_line_numbers_for_bad_lines():
    text = "term: A | B\n\nfoo: bar\nban: only-phrase\nacronym: lower | Thing\n"
    glossary = lexicon.parse_glossary(text)
    assert [number for number, _ in glossary.errors] == [3, 4, 5]


def test_duplicate_term_and_alias_conflict_are_errors():
    text = "term: GitHub | Github\nterm: github | Other\nterm: Lab | GitHub\n"
    glossary = lexicon.parse_glossary(text)
    assert [number for number, _ in glossary.errors] == [2, 3]


def test_alias_match_is_case_insensitive_with_columns():
    found = findings_for("Use Github here.\n")
    assert len(found) == 1
    assert found[0]["line"] == 1
    assert found[0]["column"] == 5
    assert found[0]["found"] == "Github"
    assert found[0]["suggestion"] == "GitHub"
    assert found[0]["rule"] == "alias"
    assert found[0]["severity"] == "error"


def test_preferred_spelling_is_not_flagged():
    assert findings_for("Use GitHub here.\n") == []


def test_matches_whole_words_only():
    assert findings_for("digitgithub and githubs\n") == []


def test_longest_match_wins_over_a_shorter_alias():
    text = "term: GitHub Pages | Github pages site\nterm: GitHub | Github\n"
    found = findings_for("Host it on Github pages site.\n", text)
    assert len(found) == 1
    assert found[0]["found"] == "Github pages site"
    assert found[0]["suggestion"] == "GitHub Pages"


def test_code_blocks_and_inline_code_are_skipped():
    text = "```\nGithub\n```\nSee `Github` only.\n"
    assert findings_for(text) == []


def test_ignore_marker_skips_only_that_line():
    found = findings_for("Github here lexicon: ignore\nGithub there\n")
    assert [f["line"] for f in found] == [2]


def test_acronym_must_be_expanded_on_first_use():
    found = findings_for("The API is fast.\nThe API again.\n")
    warnings = [f for f in found if f["severity"] == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["line"] == 1
    assert warnings[0]["rule"] == "acronym"
    assert warnings[0]["suggestion"] == "Application Programming Interface (API)"


def test_acronym_expanded_on_same_line_is_accepted():
    assert findings_for("Application Programming Interface (API) docs.\nAPI used.\n") == []


def test_banned_phrase_carries_its_reason():
    found = findings_for("Push to the Master Branch now.\n")
    assert len(found) == 1
    assert found[0]["rule"] == "ban"
    assert found[0]["suggestion"] == "main branch"
    assert "(inclusive terminology)" in found[0]["message"]


def test_apply_fixes_keeps_case_and_line_endings():
    text = "Github and Whitelist\r\nmaster branch\r\n"
    hits = scan(text).hits
    fixed = lexicon.apply_fixes(text, hits)
    assert fixed == "GitHub and Allowlist\r\nmain branch\r\n"


def test_all_caps_case_only_alias_is_fixed_to_canonical_casing():
    assert lexicon.match_case("GITHUB", "GitHub") == "GitHub"
    text = "GITHUB\n"
    fixed = lexicon.apply_fixes(text, scan(text).hits)
    assert fixed == "GitHub\n"


def test_preferred_term_counts_toward_coverage_case_insensitively():
    usage = scan("Sign in and Github.\n").usage
    assert usage["sign in"] == 1
    assert usage["GitHub"] == 1


def test_collect_files_walks_directories_and_skips_glossary(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / ".hidden").mkdir()
    glossary = tmp_path / "glossary.txt"
    glossary.write_text(GLOSSARY, encoding="utf-8")
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    (tmp_path / "c.py").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "d.rst").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden" / "e.md").write_text("x", encoding="utf-8")
    files = lexicon.collect_files([str(tmp_path)], str(glossary))
    assert sorted(os.path.basename(f) for f in files) == ["a.md", "b.txt", "d.rst"]


def test_collect_files_rejects_missing_path():
    with pytest.raises(lexicon.UsageError):
        lexicon.collect_files(["does-not-exist"], "glossary.txt")


def test_check_exit_codes_for_findings_and_clean_docs(tmp_path, capsys):
    glossary = write_glossary(tmp_path)
    dirty = tmp_path / "dirty.md"
    dirty.write_text("Github here.\n", encoding="utf-8")
    clean = tmp_path / "clean.md"
    clean.write_text("GitHub here.\n", encoding="utf-8")
    assert lexicon.main(["check", glossary, str(dirty)]) == 1
    assert f"{dirty}:1:1: error:" in capsys.readouterr().out
    assert lexicon.main(["check", glossary, str(clean)]) == 0


def test_strict_turns_warnings_into_failure(tmp_path):
    glossary = write_glossary(tmp_path)
    doc = tmp_path / "doc.md"
    doc.write_text("The API is here.\n", encoding="utf-8")
    assert lexicon.main(["check", glossary, str(doc)]) == 0
    assert lexicon.main(["check", glossary, str(doc), "--strict"]) == 1


def test_json_output_is_valid_and_has_coverage(tmp_path, capsys):
    glossary = write_glossary(tmp_path)
    doc = tmp_path / "doc.md"
    doc.write_text("Github here.\n", encoding="utf-8")
    assert lexicon.main(["check", glossary, str(doc), "--format", "json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["errors"] == 1
    assert report["findings"][0]["rule"] == "alias"
    assert report["coverage"]["GitHub"] == 1
    assert report["coverage"]["allowlist"] == 0


def test_coverage_lists_unused_entries(tmp_path, capsys):
    glossary = write_glossary(tmp_path)
    doc = tmp_path / "doc.md"
    doc.write_text("GitHub here.\n", encoding="utf-8")
    lexicon.main(["check", glossary, str(doc), "--coverage"])
    out = capsys.readouterr().out
    assert "unused entries:" in out
    assert "allowlist" in out


def test_fix_diff_writes_nothing_and_fix_rewrites(tmp_path, capsys):
    glossary = write_glossary(tmp_path)
    doc = tmp_path / "doc.md"
    doc.write_text("Github here.\n", encoding="utf-8")
    assert lexicon.main(["fix", glossary, str(doc), "--diff"]) == 1
    diff = capsys.readouterr().out
    assert "-Github here." in diff
    assert "+GitHub here." in diff
    assert doc.read_text(encoding="utf-8") == "Github here.\n"
    assert lexicon.main(["fix", glossary, str(doc)]) == 0
    assert doc.read_text(encoding="utf-8") == "GitHub here.\n"


def test_bad_glossary_exits_with_usage_error(tmp_path, capsys):
    glossary = write_glossary(tmp_path, "nonsense\n")
    doc = tmp_path / "doc.md"
    doc.write_text("Github here.\n", encoding="utf-8")
    assert lexicon.main(["check", glossary, str(doc)]) == 2
    assert f"{glossary}:1: error:" in capsys.readouterr().err
