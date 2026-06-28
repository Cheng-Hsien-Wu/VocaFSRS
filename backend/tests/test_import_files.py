import pytest
from fastapi import HTTPException

from app.services.import_files import parse_upload, split_txt_vocabulary_line, suggest_mapping


def test_parse_txt_upload_supports_toeic_style_lines():
    contents = (
        "a board member\t委員會/董事會成員\n"
        "a dedicated and talented team  專注且具備才能的團隊\n"
        "abandon 放棄\n"
    ).encode("utf-8")

    headers, rows, stored_contents, encoding = parse_upload("TOEIC-Vocabulary.txt", contents)

    assert headers == ["english", "chinese"]
    assert rows == [
        ["a board member", "委員會/董事會成員"],
        ["a dedicated and talented team", "專注且具備才能的團隊"],
        ["abandon", "放棄"],
    ]
    assert stored_contents.decode("utf-8").splitlines()[0] == "english,chinese"
    assert encoding == "utf-8"


def test_parse_csv_upload_preserves_headers_and_rows():
    headers, rows, stored_contents, encoding = parse_upload(
        "words.csv",
        "english,chinese,pos\nfatigue,疲勞,noun\n".encode("utf-8"),
    )

    assert headers == ["english", "chinese", "pos"]
    assert rows == [["fatigue", "疲勞", "noun"]]
    assert stored_contents == "english,chinese,pos\nfatigue,疲勞,noun\n".encode("utf-8")
    assert encoding == "utf-8"


def test_suggest_mapping_uses_known_aliases():
    assert suggest_mapping(["word", "meaning", "pos"]) == {
        "english": "word",
        "chinese_meaning": "meaning",
        "part_of_speech": "pos",
    }


def test_txt_line_without_separator_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        split_txt_vocabulary_line("abandon", 1)

    assert exc_info.value.status_code == 400
