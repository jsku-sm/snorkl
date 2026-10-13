from pathlib import Path
import uuid
import pytest

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def button(at, label):
    return next(b for b in at.button if b.label == label)


def test_course_activity_filters_preview_and_attempt_tabs():
    at = AppTest.from_file(APP, default_timeout=20).run()
    assert not at.exception
    button(at, "＋ 수업 만들기").click().run()
    title = "화면 테스트 " + uuid.uuid4().hex[:8]
    next(x for x in at.text_input if x.label == "수업 이름").input(title)
    button(at, "수업 만들기").click().run()
    button(at, title).click().run()
    button(at, "＋ 활동 추가하기").click().run()
    next(x for x in at.text_input if x.label == "활동 이름").input("중심 찾기")
    for label, value in [("학생에게 제시할 문제", "원의 중심을 구하세요"), ("모범답안", "(2,-1)"), ("평가 기준 · 한 줄에 하나", "부호를 설명한다")]:
        next(x for x in at.text_area if x.label == label).input(value)
    button(at, "활동 저장").click().run()
    assert not at.exception
    next(x for x in at.selectbox if x.label == "상태").select("보기만 가능").run()
    next(x for x in at.radio if x.label == "활동 상태").set_value("진행 중").run()
    assert not any(b.label == "중심 찾기" for b in at.button)
    next(x for x in at.radio if x.label == "활동 상태").set_value("전체").run()
    next(x for x in at.selectbox if x.label == "상태").select("진행 중").run()
    button(at, "중심 찾기").click().run()
    next(x for x in at.radio if x.label == "활동 화면").set_value("학생으로 체험해보기").run()
    assert not at.exception
    for answer in ["첫 풀이", "다시 작성한 풀이"]:
        next(x for x in at.text_area if x.label == "나의 답안").input(answer)
        button(at, "답안 제출").click().run()
        assert not at.exception
    assert [t.label for t in at.tabs] == ["1번째 시도", "2번째 시도"]
    assert any(x.value == "첫 풀이" for x in at.markdown)
    assert any(x.value == "다시 작성한 풀이" for x in at.markdown)


def test_teacher_opens_all_attempts_and_student_reads_saved_feedback():
    script = '''
import streamlit as st
from think_app.storage import MemoryStore
from think_app.courses import CourseManager, now
from think_app.classroom import Classroom
from think_app.classroom_ui import course_detail, shared_student
from think_app.config import Settings
import uuid
if "store" not in st.session_state:
    store = MemoryStore()
    manager = CourseManager(store)
    cid = manager.create("수학", "#6948f5", ["1반"])
    room = Classroom(store)
    aid = room.save_activity(cid, "중심 찾기", "중심은?", "(2,-1)", ["부호 설명"], 5, now()[:10], None)
    for text in ["첫 답안", "수정 답안"]:
        room.submit(aid, "10101", "김학생", "1반", text, [], False, uuid.uuid4().hex)
    st.session_state.store = store
    st.session_state.cid = cid
    st.session_state.selected_activity = aid
course = CourseManager(st.session_state.store).get(st.session_state.cid)
if st.session_state.get("student_view"):
    shared_student(st.session_state.store, course, Settings())
else:
    course_detail(st.session_state.store, course, Settings())
'''
    at = AppTest.from_string(script, default_timeout=20).run()
    assert not at.exception
    button(at, "김학생 · 2번째 시도").click().run()
    assert [t.label for t in at.tabs] == ["1번째 시도", "2번째 시도"]
    at.text_area[0].input("첫 시도에서 부호를 다시 확인해보세요.")
    next(b for b in at.button if b.label == "피드백 저장").click().run()
    assert not at.exception
    at.session_state["student_view"] = True
    at.run()
    next(x for x in at.text_input if x.label == "학번").input("10101")
    next(x for x in at.text_input if x.label == "이름").input("김학생")
    button(at, "수업 참여하기").click().run()
    assert not at.exception
    assert any(x.value == "첫 시도에서 부호를 다시 확인해보세요." for x in at.markdown)


@pytest.mark.parametrize("mode, options", [("quiz", ["y=x", "y=2x+1"]), ("quiz", []), ("graph", [])])
def test_student_mode_selects_correct_editor_and_quiz_saves(mode, options):
    script = f'''
import streamlit as st
from think_app.storage import MemoryStore
from think_app.courses import CourseManager, now
from think_app.classroom import Classroom
from think_app.classroom_ui import student_preview
from think_app.ai import AI
from think_app.config import Settings
if "store" not in st.session_state:
    st.session_state.store = MemoryStore()
    st.session_state.cid = CourseManager(st.session_state.store).create("수학", "#6948f5", ["1반"])
    st.session_state.aid = Classroom(st.session_state.store).save_activity(st.session_state.cid,
        "함수", "직선을 쓰세요", "y=2x+1", ["기울기 설명"], 5, now()[:10], None, mode={mode!r}, quiz_options={options!r})
room = Classroom(st.session_state.store)
student_preview(room, CourseManager(st.session_state.store).get(st.session_state.cid), room.activity(st.session_state.aid), AI(Settings()))
'''
    at = AppTest.from_string(script, default_timeout=20).run()
    assert not at.exception
    if mode == "graph":
        assert not button(at, "답안 제출").disabled
        assert any("‘캡처하기’" in caption.value for caption in at.caption)
    else:
        if options:
            next(x for x in at.radio if x.label == "퀴즈 답 선택").set_value("y=2x+1")
        else:
            next(x for x in at.text_input if x.label == "퀴즈 답").input("y=2x+1")
        button(at, "답안 제출").click().run()
        assert not at.exception
        assert any(x.value == "y=2x+1" for x in at.markdown)
        assert any(t.label == "1번째 시도" for t in at.tabs)


def test_desmos_course_name_opens_current_screen_editor_without_extra_activity_setup():
    at = AppTest.from_file(APP, default_timeout=20).run()
    button(at, "＋ 수업 만들기").click().run()
    title = "데스모스 함수 " + uuid.uuid4().hex[:8]
    next(x for x in at.text_input if x.label == "수업 이름").input(title)
    button(at, "수업 만들기").click().run()
    button(at, title).click().run()
    assert not at.exception
    assert next(x for x in at.radio if x.label == "수업 화면").value == "작업하기"
    activity = next(x for x in at.selectbox if x.label == "작업할 활동")
    assert activity.options == ["데스모스 그래프"]
    assert any("현재 화면에서 데스모스" in x.value for x in at.caption)
    assert button(at, "답안 제출").disabled
    assert any(x.label == "학번" for x in at.text_input)
    at.run()
    assert next(x for x in at.selectbox if x.label == "작업할 활동").options == ["데스모스 그래프"]
    button(at, "01 · 수업 활동").click().run()
    assert not at.exception
    assert button(at, "데스모스 그래프")
    button(at, "02 · 학생 제출함").click().run()
    assert not at.exception
    assert next(x for x in at.radio if x.label == "수업 화면").value == "학생 제출함"


def test_workbench_real_submission_is_visible_to_teacher_and_save_failure_keeps_answer():
    script = '''
import streamlit as st
from think_app.storage import MemoryStore, AppError
from think_app.courses import CourseManager, now
from think_app.classroom import Classroom
from think_app.classroom_ui import course_detail
from think_app.config import Settings
if "store" not in st.session_state:
    st.session_state.store = MemoryStore()
    st.session_state.cid = CourseManager(st.session_state.store).create("수학 퀴즈", "#4384ff", ["1반"])
    st.session_state.aid = Classroom(st.session_state.store).save_activity(st.session_state.cid,
        "기울기 퀴즈", "y=2x+1의 기울기는?", "2", ["기울기 설명"], 5, now()[:10], None,
        mode="quiz", quiz_options=["1", "2"])
original_save = st.session_state.store.save_student_submission
if st.session_state.get("fail_save"):
    def fail_save(attempt):
        raise AppError("학생 제출 파일 저장 실패")
    st.session_state.store.save_student_submission = fail_save
try:
    course_detail(st.session_state.store, CourseManager(st.session_state.store).get(st.session_state.cid), Settings())
finally:
    st.session_state.store.save_student_submission = original_save
'''
    at = AppTest.from_string(script, default_timeout=20).run()
    next(x for x in at.radio if x.label == "수업 화면").set_value("작업하기").run()
    next(x for x in at.text_input if x.label == "학번").input("10101")
    next(x for x in at.text_input if x.label == "이름").input("김학생")
    at.run()
    next(x for x in at.radio if x.label == "퀴즈 답 선택").set_value("2")
    next(x for x in at.text_area if x.label == "나의 답안").input("x의 계수가 2이므로 기울기는 2입니다.")
    at.session_state["fail_save"] = True
    button(at, "답안 제출").click().run()
    assert not at.exception
    assert any("저장 실패" in x.value for x in at.error)
    assert next(x for x in at.text_area if x.label == "나의 답안").value.startswith("x의 계수")
    assert not at.session_state["store"].read().get("course_attempts")
    at.session_state["fail_save"] = False
    button(at, "답안 제출").click().run()
    assert not at.exception
    attempts = list(at.session_state["store"].read()["course_attempts"].values())
    assert len(attempts) == 1 and attempts[0]["preview"] is False
    assert attempts[0]["quiz_answer"] == "2"
    assert attempts[0]["submission_file"].startswith("student-submissions/")
    assert any("선생님에게" in x.value for x in at.success)
    next(x for x in at.radio if x.label == "수업 화면").set_value("학생 제출함").run()
    assert not at.exception
    assert button(at, "김학생 · 1번째 시도")
    next(x for x in at.radio if x.label == "수업 화면").set_value("작업하기").run()
    assert next(x for x in at.text_input if x.label == "학번").value == "10101"
    assert next(x for x in at.text_input if x.label == "이름").value == "김학생"
