from datetime import datetime, timedelta, timezone
import uuid

import pytest
from cryptography.fernet import Fernet
from streamlit.testing.v1 import AppTest

import think_app.classroom as classroom_module
from think_app.classroom import Classroom
from think_app.courses import CourseManager, KST
from think_app.storage import AppError, LocalStore, MemoryStore


@pytest.fixture
def clock(monkeypatch):
    value = {"instant": datetime(2026, 10, 13, 9, 0, tzinfo=KST)}
    monkeypatch.setattr(classroom_module, "now", lambda: value["instant"].isoformat(timespec="seconds"))
    return value


def setup_room(duration=5, store=None):
    store = store or MemoryStore()
    cid = CourseManager(store).create("수학", "#6948f5", ["1반"])
    room = Classroom(store)
    aid = room.save_activity(cid, "수행평가", "기울기는?", "2", ["기울기 설명"], 5,
                             classroom_module.now()[:10], None, duration_minutes=duration)
    return room, cid, aid


def submit(room, aid, request=None, images=None):
    return room.submit(aid, "10101", "김학생", "1반", "기울기는 2", images or [], False,
                       request or uuid.uuid4().hex)


def test_shared_timer_waits_for_distribution_and_enforces_exact_deadline(clock):
    room, _, aid = setup_room()
    assert room.timer_state(room.activity(aid))["phase"] == "waiting"
    assert not room._can_submit(room.activity(aid))
    with pytest.raises(AppError, match="제출할 수 없는"):
        submit(room, aid)
    room.start_timer(aid)
    task = room.activity(aid)
    assert datetime.fromisoformat(task["timer_deadline"]) == clock["instant"].astimezone(timezone.utc) + timedelta(minutes=5)
    assert room.timer_state(task)["remaining_seconds"] == 300
    first = submit(room, aid)
    clock["instant"] += timedelta(minutes=4, seconds=59)
    assert room.timer_state(room.activity(aid))["remaining_seconds"] == 1
    submit(room, aid)
    clock["instant"] += timedelta(seconds=1)
    assert room.timer_state(room.activity(aid))["phase"] == "expired"
    with pytest.raises(AppError, match="제출할 수 없는"):
        submit(room, aid)
    assert len(room.attempts(aid)) == 2
    assert submit(room, aid, first) == first  # Network retries remain idempotent after the deadline.


def test_teacher_adjusts_remaining_time_including_after_expiry(clock):
    room, _, aid = setup_room()
    with pytest.raises(AppError, match="시작한 뒤"):
        room.adjust_timer(aid, 2)
    room.start_timer(aid)
    started_at = room.activity(aid)["timer_started_at"]
    clock["instant"] += timedelta(minutes=1)
    room.adjust_timer(aid, 2)
    assert room.timer_state(room.activity(aid))["remaining_seconds"] == 120
    clock["instant"] += timedelta(minutes=2)
    assert not room._can_submit(room.activity(aid))
    room.adjust_timer(aid, 3)
    assert room._can_submit(room.activity(aid))
    assert room.timer_state(room.activity(aid))["remaining_seconds"] == 180
    assert room.activity(aid)["timer_started_at"] == started_at
    submit(room, aid)
    room.adjust_timer(aid, 0)
    assert not room._can_submit(room.activity(aid))
    room.adjust_timer(aid, 1)
    assert room._can_submit(room.activity(aid))
    room.end_timer(aid)
    assert room.timer_state(room.activity(aid))["phase"] == "ended"
    with pytest.raises(AppError, match="종료한"):
        room.adjust_timer(aid, 1)
    with pytest.raises(AppError, match="이미 시작"):
        room.start_timer(aid)


def test_deadline_is_rechecked_inside_submission_transaction(clock):
    class ExpiringStore(MemoryStore):
        expire_on_blob = False

        def put_blob(self, name, data):
            super().put_blob(name, data)
            if self.expire_on_blob:
                clock["instant"] += timedelta(minutes=5)

    room, _, aid = setup_room(store=ExpiringStore())
    room.start_timer(aid)
    room.store.expire_on_blob = True
    with pytest.raises(AppError, match="제출할 수 없는"):
        submit(room, aid, images=[b"answer image"])
    assert not room.attempts(aid)
    assert not room.store.submissions


def test_running_timer_survives_activity_edits_and_server_restart(clock, tmp_path):
    key = Fernet.generate_key().decode()
    room, cid, aid = setup_room(store=LocalStore(tmp_path, key))
    room.start_timer(aid)
    room.adjust_timer(aid, 7)
    original = room.activity(aid)
    room.save_activity(cid, "수정된 제목", "기울기는?", "2", ["기울기 설명"], 5,
                       classroom_module.now()[:10], None, aid)
    restarted = Classroom(LocalStore(tmp_path, key))
    task = restarted.activity(aid)
    for field in ("duration_minutes", "timer_started_at", "timer_deadline", "timer_ended_at"):
        assert task[field] == original[field]
    assert restarted.timer_state(task)["remaining_seconds"] == 420
    with pytest.raises(AppError, match="타이머에서"):
        room.save_activity(cid, "수정된 제목", "기울기는?", "2", ["기울기 설명"], 5,
                           classroom_module.now()[:10], None, aid, duration_minutes=8)


def test_untimed_activities_remain_compatible_and_can_be_configured_before_start(clock):
    room, cid, aid = setup_room(duration=0)
    assert room._can_submit(room.activity(aid))
    submit(room, aid)
    room.save_activity(cid, "수행평가", "기울기는?", "2", ["기울기 설명"], 5,
                       classroom_module.now()[:10], None, aid, duration_minutes=10)
    assert room.timer_state(room.activity(aid))["phase"] == "waiting"
    room.save_activity(cid, "수행평가", "기울기는?", "2", ["기울기 설명"], 5,
                       classroom_module.now()[:10], None, aid, duration_minutes=0)
    assert room._can_submit(room.activity(aid))
    legacy = room.activity(aid)
    legacy.pop("duration_minutes")
    assert room._can_submit(legacy)


def test_distribution_can_start_legacy_untimed_activity_with_teacher_duration(clock):
    room, _, aid = setup_room(duration=0)
    room.store.transaction(lambda state: state["course_activities"][aid].pop("duration_minutes"))
    room.start_timer(aid, duration_minutes=12)
    task = room.activity(aid)
    assert task["duration_minutes"] == 12
    assert room.timer_state(task)["remaining_seconds"] == 720
    assert room._can_submit(task)


@pytest.mark.parametrize("change", [{"start": "2026-10-14"}, {"end": "2026-10-12"},
                                    {"status": "숨김"}, {"status": "보기만 가능"}])
def test_invalid_distribution_preserves_existing_duration_and_timer(clock, change):
    room, _, aid = setup_room(duration=0)
    room.store.transaction(lambda state: state["course_activities"][aid].update(change))
    original = room.activity(aid)
    with pytest.raises(AppError, match="상태와 시작일"):
        room.start_timer(aid, duration_minutes=12)
    assert room.activity(aid) == original


def test_distribution_cannot_reset_started_timer_or_use_zero_minutes(clock):
    room, _, aid = setup_room(duration=0)
    original = room.activity(aid)
    with pytest.raises(AppError, match="풀이 시간"):
        room.start_timer(aid, duration_minutes=0)
    assert room.activity(aid) == original
    room.start_timer(aid, duration_minutes=12)
    original = room.activity(aid)
    with pytest.raises(AppError, match="이미 시작"):
        room.start_timer(aid, duration_minutes=30)
    assert room.activity(aid) == original


@pytest.mark.parametrize("duration", [-1, 1441, 1.5, True, "5"])
def test_timer_duration_validation(clock, duration):
    with pytest.raises(AppError, match="풀이 시간"):
        setup_room(duration)


def test_timer_uses_utc_instants_and_korean_course_day(clock):
    clock["instant"] = datetime(2026, 10, 13, 0, 5, tzinfo=KST)
    room, _, aid = setup_room(duration=10)
    room.start_timer(aid)
    assert room.activity(aid)["timer_deadline"] == "2026-10-12T15:15:00+00:00"
    assert room._can_submit(room.activity(aid))
    room.set_status(aid, "보기만 가능")
    assert not room._can_submit(room.activity(aid))
    room.set_status(aid, "진행 중")
    room.store.transaction(lambda state: state["course_activities"][aid].update(timer_deadline="invalid"))
    assert not room._can_submit(room.activity(aid))


@pytest.mark.parametrize("duration", [0, 5])
def test_teacher_timer_controls_start_adjust_end_and_student_view(clock, duration):
    script = f'''
import streamlit as st
from think_app.storage import MemoryStore
from think_app.courses import CourseManager
from think_app.classroom import Classroom, now
from think_app.timer_ui import teacher_timer_controls, student_timer
if "store" not in st.session_state:
    st.session_state.store = MemoryStore()
    cid = CourseManager(st.session_state.store).create("수학", "#6948f5", ["1반"])
    st.session_state.aid = Classroom(st.session_state.store).save_activity(cid,
        "수행평가", "기울기는?", "2", ["기울기 설명"], 5, now()[:10], None, duration_minutes={duration})
room = Classroom(st.session_state.store)
teacher_timer_controls(room, room.activity(st.session_state.aid))
student_timer(room, st.session_state.aid)
'''
    at = AppTest.from_string(script, default_timeout=20).run()
    assert not at.exception
    if duration:
        assert any("시작 대기" in element.value for element in at.info)
    distribution = next(element for element in at.number_input if element.label == "배포할 풀이 시간(분)")
    assert distribution.value == (duration or 30)
    distribution.set_value(6)
    next(button for button in at.button if button.label == "평가 시작 · 배포").click().run()
    assert not at.exception
    at.run()
    assert all(metric.value == "06:00" for metric in at.metric)
    next(element for element in at.number_input if element.label == "남은 풀이 시간(분)").set_value(8)
    next(button for button in at.button if button.label == "남은 시간 적용").click().run()
    assert not at.exception
    assert all(metric.value == "08:00" for metric in at.metric)
    next(button for button in at.button if button.label == "평가 종료").click().run()
    assert not at.exception
    assert all(metric.value == "00:00" for metric in at.metric)
    assert any("종료했습니다" in element.value for element in at.warning)
