import io
from types import SimpleNamespace
import zipfile

import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from streamlit.testing.v1 import AppTest

from think_app.ai import AI
from think_app.config import Settings
from think_app.model_answers import answer_text, extract_model_answer, search_model_answer
from think_app.storage import AppError


def docx_bytes(body, encoding="utf-8"):
    xml = (f'<?xml version="1.0" encoding="{encoding}"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
           'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
           '<w:body>' + body + '</w:body></w:document>')
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", xml.encode(encoding))
    return output.getvalue()


def pdf_bytes(text="The vertex is (2,-1).", blank=False, encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    if not blank:
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        content = DecodedStreamObject()
        escaped = text.replace("(", "\\(").replace(")", "\\)")
        content.set_data(("BT /F1 12 Tf 30 260 Td (" + escaped + ") Tj ET").encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
    if encrypted:
        writer.encrypt("secret")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class Responses:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def connected_ai(response):
    responses = Responses(response)
    return AI(Settings(ai_enabled=True, api_key="test-key"), client=SimpleNamespace(responses=responses)), responses


@pytest.mark.parametrize("filename, encoding", [("모범답안.txt", "utf-8-sig"), ("풀이.MD", "cp949"), ("답안.txt", "utf-16")])
def test_text_files_preserve_korean_and_normalize_newlines(filename, encoding):
    text = "  꼭짓점은 (2, -1)이다.\r\n기울기를 설명한다.  "
    assert extract_model_answer(filename, text.encode(encoding)) == "꼭짓점은 (2, -1)이다.\n기울기를 설명한다."


def test_docx_keeps_paragraphs_tables_and_equation_text():
    raw = docx_bytes('<w:p><w:r><w:t>완전제곱식</w:t><w:tab/><w:t>변형</w:t></w:r></w:p>'
                     '<w:tbl><w:tr><w:tc><w:p><m:oMath><m:r><m:t>(x-2)²-1</m:t></m:r></m:oMath></w:p></w:tc></w:tr></w:tbl>')
    assert extract_model_answer("답안.docx", raw) == "완전제곱식\t변형\n(x-2)²-1"


def test_docx_preserves_fraction_exponent_and_square_root_structure():
    raw = docx_bytes('<w:p><m:oMath>'
                     '<m:f><m:num><m:r><m:t>1</m:t></m:r></m:num>'
                     '<m:den><m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e>'
                     '<m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup></m:den></m:f>'
                     '<m:r><m:t> + </m:t></m:r>'
                     '<m:rad><m:deg/><m:e><m:r><m:t>3</m:t></m:r></m:e></m:rad>'
                     '</m:oMath></w:p>')
    assert extract_model_answer("수식.docx", raw) == r"\frac{1}{{x}^{2}} + \sqrt{3}"


def test_pdf_extracts_actual_text_and_rejects_scanned_or_encrypted_files():
    assert extract_model_answer("답안.pdf", pdf_bytes()) == "The vertex is (2,-1)."
    assert extract_model_answer("최대답안.pdf", pdf_bytes("x" * 10000)) == "x" * 10000
    with pytest.raises(AppError, match="스캔"):
        extract_model_answer("스캔.pdf", pdf_bytes(blank=True))
    with pytest.raises(AppError, match="암호"):
        extract_model_answer("암호.pdf", pdf_bytes(encrypted=True))


@pytest.mark.parametrize("name, raw, message", [
    ("풀이.hwp", b"answer", "HWP"), ("답안.txt", b"", "비어"),
    ("답안.txt", b"a" * 10_000_001, "10MB"), ("답안.txt", b"a" * 10001, "10,000자"),
    ("답안.docx", b"not a zip", "Word"), ("답안.pdf", b"not a PDF", "PDF"),
    ("답안.txt", b"hello\x00world", "텍스트 파일 형식"), ("답안.txt", b"\xff\x00\xfe", "인코딩"),
])
def test_file_validation_returns_actionable_errors_without_partial_answer(name, raw, message):
    with pytest.raises(AppError, match=message):
        extract_model_answer(name, raw)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_docx_rejects_entity_definitions_before_parsing(encoding):
    declaration = f'<?xml version="1.0" encoding="{encoding}"?><!DOCTYPE document [<!ENTITY x "bad">]>'
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("word/document.xml", (declaration + '<document>&x;</document>').encode(encoding))
    with pytest.raises(AppError, match="형식"):
        extract_model_answer("답안.docx", raw.getvalue())


def test_image_upload_uses_configured_ai_only_and_keeps_uncertain_text_for_review():
    output = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(output, format="PNG")
    raw = output.getvalue()
    with pytest.raises(AppError, match="AI 연결"):
        extract_model_answer("답안.png", raw, AI(Settings()))
    ai, responses = connected_ai({"status": "completed", "output_text": "x = 2\n[판독 불가]"})
    assert extract_model_answer("답안.png", raw, ai) == "x = 2\n[판독 불가]"
    call = responses.calls[0]
    assert call["store"] is False and call["model"] == ai.settings.model
    assert call["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,")
    ai, _ = connected_ai({"status": "incomplete", "output_text": "partial"})
    with pytest.raises(AppError, match="완료되지"):
        extract_model_answer("답안.png", raw, ai)


def sourced_response(status="completed", search_status="completed", citations=True, url="https://example.org/lesson"):
    text = "완전제곱식을 만들면 꼭짓점은 (2, -1)입니다. [1]"
    annotation = {"type": "url_citation", "title": "함수 풀이", "url": url,
                  "start_index": len(text) - 3, "end_index": len(text)}
    return {"status": status, "output_text": text, "output": [
        {"type": "web_search_call", "status": search_status},
        {"type": "message", "content": [{"type": "output_text", "text": text,
                                         "annotations": [annotation] if citations else []}]}]}


def test_web_search_is_real_required_sourced_and_does_not_save_or_fetch_urls():
    ai, responses = connected_ai(sourced_response())
    draft = search_model_answer("  y=x²-4x+3의 꼭짓점  ", ai)
    assert "(2, -1)" in draft["text"]
    assert "[함수 풀이](<https://example.org/lesson>)" in draft["text"]
    assert draft["sources"] == [{"title": "함수 풀이", "url": "https://example.org/lesson"}]
    call = responses.calls[0]
    assert call["tools"][0]["type"] == "web_search" and call["tool_choice"] == "required"
    assert call["store"] is False and call["model"] == ai.settings.model
    assert call["input"] == "y=x²-4x+3의 꼭짓점"


@pytest.mark.parametrize("changes, message", [
    ({"status": "incomplete"}, "완료되지"), ({"search_status": "failed"}, "웹 검색이 완료되지"),
    ({"citations": False}, "출처"), ({"url": "javascript:alert(1)"}, "출처"),
    ({"url": "https://example.org/a\nfile"}, "출처"),
])
def test_web_search_rejects_partial_unsourced_and_invalid_link_results(changes, message):
    ai, _ = connected_ai(sourced_response(**changes))
    with pytest.raises(AppError, match=message):
        search_model_answer("꼭짓점", ai)


def test_web_search_requires_connection_and_valid_query_before_network():
    with pytest.raises(AppError, match="AI 연결"):
        search_model_answer("문제", AI(Settings()))
    ai, responses = connected_ai(sourced_response())
    for query in ("", " " * 10, "a" * 2001):
        with pytest.raises(AppError, match="검색어"):
            search_model_answer(query, ai)
    assert not responses.calls


@pytest.mark.parametrize("ai_expression", ["AI(Settings())", "None"])
def test_ui_draft_is_applied_only_on_request_then_remains_editable(ai_expression):
    script = '''
import streamlit as st
from think_app.model_answers import render_model_answer
from think_app.ai import AI
from think_app.config import Settings
key = render_model_answer({"expected": "기존 모범답안"}, AI_EXPRESSION, "activity_test")
value = st.text_area("모범답안", key=key, max_chars=10000)
if st.button("활동 저장"):
    st.session_state.saved_expected = value
'''
    at = AppTest.from_string(script.replace("AI_EXPRESSION", ai_expression)).run()
    assert not at.exception
    assert next(x for x in at.text_area if x.label == "모범답안").value == "기존 모범답안"
    assert next(x for x in at.button if x.label == "웹에서 모범답안 찾기").disabled
    at.session_state["activity_test_answer_import_draft"] = "가져온 풀이"
    at.session_state["activity_test_answer_import_metadata"] = {"label": "파일: 풀이.txt", "sources": []}
    at.run()
    assert next(x for x in at.text_area if x.label == "모범답안").value == "기존 모범답안"
    next(x for x in at.text_area if x.label == "가져온 내용 확인·수정").input("확인한 풀이")
    next(x for x in at.button if x.label == "모범답안에 적용").click().run()
    assert not at.exception
    assert next(x for x in at.text_area if x.label == "모범답안").value == "확인한 풀이"
    next(x for x in at.text_area if x.label == "모범답안").input("최종 풀이")
    next(x for x in at.button if x.label == "활동 저장").click().run()
    assert at.session_state["saved_expected"] == "최종 풀이"


def test_answer_limit_allows_exact_maximum_and_rejects_empty():
    assert answer_text("x" * 10000) == "x" * 10000
    with pytest.raises(AppError, match="글이 없습니다"):
        answer_text(" \n ")
