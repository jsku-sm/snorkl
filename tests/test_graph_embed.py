import uuid

import pytest

from think_app.classroom import Classroom
from think_app.courses import CourseManager, now
from think_app.storage import AppError, MemoryStore


def graph_activity():
    store = MemoryStore()
    cid = CourseManager(store).create("데스모스", "#6948f5", ["1반"])
    room = Classroom(store)
    return room, room.ensure_desmos_activity(CourseManager(store).get(cid))


def test_embedded_desmos_saves_actual_explanation_without_fabricating_graph_capture():
    room, aid = graph_activity()
    room.submit(aid, "10101", "학생", "1반", "y=2x+1, 기울기는 2입니다.", [], False,
                uuid.uuid4().hex, graph={"mode": "embed"})
    saved = room.attempts(aid)[0]
    assert saved["graph_source"] == "embed"
    assert saved["graph"] is None
    assert saved["images"] == []
    assert saved["text"] == "y=2x+1, 기울기는 2입니다."


def test_embedded_desmos_still_requires_student_content_and_sdk_requires_capture():
    room, aid = graph_activity()
    with pytest.raises(AppError, match="답안을 입력"):
        room.submit(aid, "10101", "학생", "1반", "", [], False, uuid.uuid4().hex, graph={"mode": "embed"})
    with pytest.raises(AppError, match="그래프"):
        room.submit(aid, "10101", "학생", "1반", "설명", [], False, uuid.uuid4().hex, graph={"ready": False})
    assert not room.attempts(aid)
