from datetime import date, timedelta

from streamlit.testing.v1 import AppTest

from think_app.courses import now


SETUP = '''
import streamlit as st
from think_app.storage import MemoryStore
from think_app.courses import CourseManager, now
from think_app.classroom import Classroom
from think_app.classroom_ui import activity_form, course_detail, student_preview
from think_app.config import Settings
from think_app.ai import AI
if "store" not in st.session_state:
    st.session_state.store = MemoryStore()
    st.session_state.cid = CourseManager(st.session_state.store).create("수학", "#6948f5", ["1반"])
room = Classroom(st.session_state.store)
course = CourseManager(st.session_state.store).get(st.session_state.cid)
'''


def widget(items, label):
    return next(item for item in items if item.label == label)


def test_same_day_deadline_follows_selected_start_and_can_be_overridden():
    at = AppTest.from_string(SETUP + '\nactivity_form(room, course["id"], ai=AI(Settings()))', default_timeout=20).run()
    assert not at.exception
    today = date.fromisoformat(now()[:10])
    assert widget(at.checkbox, "마감일을 시작일과 동일하게").value
    assert widget(at.date_input, "시작일").value == today
    assert widget(at.date_input, "마감일 · 선택").value == today
    widget(at.text_input, "활동 이름").input("함수 평가").run()
    selected = today + timedelta(days=2)
    widget(at.date_input, "시작일").set_value(selected).run()
    assert not at.exception
    assert widget(at.date_input, "마감일 · 선택").value == selected
    assert widget(at.text_input, "활동 이름").value == "함수 평가"
    widget(at.checkbox, "마감일을 시작일과 동일하게").uncheck().run()
    widget(at.date_input, "마감일 · 선택").set_value(selected + timedelta(days=1)).run()
    widget(at.text_area, "학생에게 제시할 문제").input("기울기는?")
    widget(at.text_area, "모범답안").input("2")
    widget(at.text_area, "평가 기준 · 한 줄에 하나").input("기울기 설명")
    widget(at.number_input, "문제 풀이 시간 · 분").set_value(20)
    widget(at.button, "활동 저장").click().run()
    assert not at.exception
    saved = next(iter(at.session_state["store"].read()["course_activities"].values()))
    assert saved["end"] == (selected + timedelta(days=1)).isoformat()
    assert saved["duration_minutes"] == 20


TIMED_ACTIVITY = '''
if "aid" not in st.session_state:
    st.session_state.aid = room.save_activity(course["id"], "기울기 평가", "기울기는?", "2", ["근거 설명"],
        5, now()[:10], now()[:10], mode="quiz", quiz_options=["1", "2"], duration_minutes=10)
'''


def test_student_keeps_draft_when_timer_expires_and_can_submit_after_extension():
    at = AppTest.from_string(SETUP + TIMED_ACTIVITY + '''
student_preview(room, course, room.activity(st.session_state.aid), AI(Settings()), ("10101", "김학생", "1반"))
''', default_timeout=20).run()
    assert not at.exception
    assert widget(at.button, "답안 제출").disabled
    widget(at.text_area, "나의 답안").input("x의 계수가 2입니다.").run()
    widget(at.radio, "퀴즈 답 선택").set_value("2").run()
    from think_app.classroom import Classroom
    room = Classroom(at.session_state["store"])
    aid = at.session_state["aid"]
    room.start_timer(aid)
    at.run()
    assert not at.exception
    assert not widget(at.button, "답안 제출").disabled
    assert widget(at.metric, "남은 풀이 시간").value.startswith("09:") or widget(at.metric, "남은 풀이 시간").value == "10:00"
    room.adjust_timer(aid, 0)
    at.run()
    assert not at.exception
    assert widget(at.button, "답안 제출").disabled
    assert widget(at.text_area, "나의 답안").value == "x의 계수가 2입니다."
    room.adjust_timer(aid, 5)
    at.run()
    assert not at.exception
    assert not widget(at.button, "답안 제출").disabled
    assert widget(at.radio, "퀴즈 답 선택").value == "2"
    widget(at.button, "답안 제출").click().run()
    assert not at.exception
    assert room.attempts(aid)[0]["text"] == "x의 계수가 2입니다."


def test_teacher_distribution_controls_start_and_adjust_the_shared_timer():
    at = AppTest.from_string(SETUP + TIMED_ACTIVITY + '''
st.session_state.selected_activity = st.session_state.aid
course_detail(st.session_state.store, course, Settings())
''', default_timeout=20).run()
    assert not at.exception
    widget(at.button, "평가 시작 · 배포").click().run()
    assert not at.exception
    widget(at.number_input, "남은 풀이 시간(분)").set_value(15)
    widget(at.button, "남은 시간 적용").click().run()
    assert not at.exception
    from think_app.classroom import Classroom
    room = Classroom(at.session_state["store"])
    timer = room.timer_state(room.activity(at.session_state["aid"]))
    assert 895 <= timer["remaining_seconds"] <= 900
