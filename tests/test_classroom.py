import io
import json
import uuid
import zipfile

import pytest
from cryptography.fernet import Fernet

from think_app.classroom import Classroom, grade
from think_app.courses import CourseManager, now
from think_app.storage import AppError, LocalStore, MemoryStore


def setup_room(store=None):
    store = store or MemoryStore()
    course = CourseManager(store).create("수학", "#6948f5", ["1반"])
    room = Classroom(store)
    aid = room.save_activity(course, "원의 중심", "중심은?", "(2,-1)", ["부호 설명"], 5, now()[:10], None)
    return room, course, aid


def submit(room, aid, text="학생 풀이", preview=False, images=None, request=None):
    return room.submit(aid, "10101", "학생", "1반", text, images or [], True, request or uuid.uuid4().hex, preview)


def test_attempts_survive_restart_and_export_includes_every_image(tmp_path):
    key = Fernet.generate_key().decode()
    room, course, aid = setup_room(LocalStore(tmp_path, key))
    first = submit(room, aid, "첫 답", images=[b"image bytes"])
    second = submit(room, aid, "수정한 답")
    room.comment(first, "부호를 살펴보세요")
    submit(room, aid, preview=True)
    room = Classroom(LocalStore(tmp_path, key))
    assert [a["number"] for a in room.attempts(aid) if not a["preview"]] == [1, 2]
    with zipfile.ZipFile(io.BytesIO(room.export(course))) as archive:
        data = json.loads(archive.read("전체기록.json"))
        assert [a["id"] for a in data["attempts"]] == [first, second]
        assert data["attempts"][0]["teacher_comment"] == "부호를 살펴보세요"
        assert archive.read(f"답안/{first}/1.jpg") == b"image bytes"
        assert "수정한 답" in archive.read("학생결과.csv").decode("utf-8-sig")


def test_filters_status_limits_and_idempotent_submission():
    room, course, aid = setup_room()
    request = uuid.uuid4().hex
    assert submit(room, aid, request=request) == submit(room, aid, request=request)
    assert len(room.attempts(aid)) == 1
    for status in ["보기만 가능", "숨김"]:
        room.set_status(aid, status)
        assert not room.activities(course, "진행 중")
        assert len(room.activities(course)) == 1
        with pytest.raises(AppError, match="제출할 수 없는"):
            submit(room, aid)
    room.set_status(aid, "진행 중")
    for _ in range(4):
        submit(room, aid)
    with pytest.raises(AppError, match="최대 시도"):
        submit(room, aid)


def test_ai_compares_submission_snapshot_and_preserves_answer_on_failure():
    room, course, aid = setup_room()
    attempt_id = submit(room, aid)
    room.save_activity(course, "바뀐 활동", "다른 문제", "다른 답", ["새 기준"], 5, now()[:10], None, aid)
    class FakeAI:
        available = True
        def feedback(self, task, attempt, images):
            assert task["expected"] == "(2,-1)"
            assert attempt["text"] == "학생 풀이"
            return dict(correctness="correct", explanation_score=4, needs_teacher_review=False)
    room.evaluate(attempt_id, FakeAI())
    assert grade(room.attempts(aid)[0]) == "탁월"
    for correct, score, label in [("correct", 3, "성취"), ("partial", 2, "발전 중"), ("incorrect", 1, "성장 중")]:
        assert grade(dict(feedback=dict(correctness=correct, explanation_score=score))) == label
    class FailedAI:
        available = False
    with pytest.raises(AppError, match="답안은 저장"):
        room.evaluate(attempt_id, FailedAI())
    assert len(room.attempts(aid)) == 1
