import io
import uuid

from PIL import Image
import pytest
from streamlit.testing.v1 import AppTest

from think_app.classroom import Classroom
from think_app.courses import CourseManager, now
from think_app.storage import AppError, MemoryStore
from think_app.uploads import UploadedFiles


def capture():
    image = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(image, "PNG")
    return image.getvalue()


def setup():
    store = MemoryStore()
    cid = CourseManager(store).create("데스모스", "#6948f5", ["1반"])
    room = Classroom(store)
    aid = room.ensure_desmos_activity(CourseManager(store).get(cid))
    upload = UploadedFiles(store).save(aid, "10101", "김학생", "1반", "캡처.png", capture())
    return room, aid, upload


def test_image_only_submission_reuses_saved_blob_and_retains_original_download():
    room, aid, upload = setup()
    assert not room.attempts(aid)
    before = set(room.store.blobs)
    room.submit(aid, "10101", "김학생", "1반", "", [], False, uuid.uuid4().hex,
                graph={"mode": "embed"}, upload_ids=[upload["id"]])
    attempt = room.attempts(aid)[0]
    assert attempt["images"] == [upload["blob_ref"]]
    assert attempt["upload_ids"] == [upload["id"]]
    assert set(room.store.blobs) == before
    assert room.store.get_blob(upload["original_ref"]) == capture()
    bundle = room.store.get_student_submission(attempt)
    assert upload["blob_ref"] in bundle["assets"]


def test_uploaded_file_ownership_limits_and_expired_timer_apply_at_submission():
    room, aid, upload = setup()
    for ids, student, error in [
        ([upload["id"]], "다른학생", "다른 학생이나 활동"),
        ([upload["id"], upload["id"]], "10101", "중복"),
        ([uuid.uuid4().hex], "10101", "저장된 업로드"),
    ]:
        with pytest.raises(AppError, match=error):
            room.submit(aid, student, "학생", "1반", "", [], False, uuid.uuid4().hex,
                        graph={"mode": "embed"}, upload_ids=ids)
    with pytest.raises(AppError, match="허용 길이"):
        room.submit(aid, "10101", "김학생", "1반", "", [b"image"] * 3, False, uuid.uuid4().hex,
                    graph={"mode": "embed"}, upload_ids=[upload["id"]])
    room.start_timer(aid, 1)
    room.adjust_timer(aid, 0)
    with pytest.raises(AppError, match="제출할 수 없는"):
        room.submit(aid, "10101", "김학생", "1반", "", [], False, uuid.uuid4().hex,
                    graph={"mode": "embed"}, upload_ids=[upload["id"]])
    assert room.store.get_blob(upload["original_ref"]) == capture()
    assert not room.attempts(aid)


def test_corrupted_saved_image_is_not_submitted():
    room, aid, upload = setup()
    room.store.blobs[upload["blob_ref"]] = b"corrupt"
    with pytest.raises(AppError, match="업로드 이미지"):
        room.submit(aid, "10101", "김학생", "1반", "", [], False, uuid.uuid4().hex,
                    graph={"mode": "embed"}, upload_ids=[upload["id"]])
    assert not room.attempts(aid)


SCRIPT = '''
import streamlit as st
from types import SimpleNamespace
from think_app.classroom_ui import answer_uploads
from think_app.classroom import Classroom
from think_app.courses import CourseManager
from think_app.storage import MemoryStore, AppError
if "store" not in st.session_state:
    st.session_state.store = MemoryStore()
    manager = CourseManager(st.session_state.store)
    cid = manager.create("데스모스", "#6948f5", ["1반"])
    st.session_state.aid = Classroom(st.session_state.store).ensure_desmos_activity(manager.get(cid))
room = Classroom(st.session_state.store)
original_put = room.store.put_blob
if st.session_state.get("fail_save"):
    def broken_put(*args):
        raise AppError("파일 저장 실패")
    room.store.put_blob = broken_put
try:
    files = [] if st.session_state.get("no_file") else [SimpleNamespace(name="캡처.png", getvalue=lambda: CAPTURE)]
    ids, saved = answer_uploads(room, room.activity(st.session_state.aid), "10101", "김학생", "1반", False, "capture_test", files)
    st.session_state.selected_files = ids
    st.session_state.files_saved = saved
    st.button("답안 제출", disabled=not saved)
finally:
    room.store.put_blob = original_put
'''


def test_ui_upload_saves_before_submit_and_deduplicates_across_reruns():
    at = AppTest.from_string("CAPTURE = " + repr(capture()) + "\n" + SCRIPT).run()
    assert not at.exception
    store = at.session_state["store"]
    assert len(store.read()["course_uploads"]) == 1
    assert not store.read().get("course_attempts")
    assert at.session_state["files_saved"]
    assert len(at.session_state["selected_files"]) == 1
    assert any("체험 모드" in value.value for value in at.info)
    assert not at.success  # No claim that a MemoryStore file was written to GitHub.
    at.run()
    assert not at.exception
    assert len(store.read()["course_uploads"]) == 1
    assert len(store.blobs) == 2
    at.session_state["no_file"] = True
    at.run()
    assert not at.session_state["selected_files"]
    assert len(store.read()["course_uploads"]) == 1
    at.multiselect[0].select(at.multiselect[0].options[0]).run()
    assert not at.exception
    assert len(at.session_state["selected_files"]) == 1


def test_ui_failed_upload_keeps_file_and_retries_without_false_success():
    at = AppTest.from_string("CAPTURE = " + repr(capture()) + "\n" + SCRIPT)
    at.session_state["fail_save"] = True
    at.run()
    assert not at.exception
    assert not at.session_state["files_saved"]
    assert not at.session_state["store"].read().get("course_uploads")
    assert next(b for b in at.button if b.label == "답안 제출").disabled
    assert any("파일 저장 실패" in e.value for e in at.error)
    at.session_state["fail_save"] = False
    next(b for b in at.button if b.label == "파일 저장 다시 시도").click().run()
    assert not at.exception
    assert at.session_state["files_saved"]
    assert len(at.session_state["store"].read()["course_uploads"]) == 1
