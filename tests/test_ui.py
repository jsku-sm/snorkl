from pathlib import Path
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def button(at,label):
    return next(b for b in at.button if b.label == label)


def test_student_submits_twice_teacher_sees_and_reviews():
    at=AppTest.from_file(APP,default_timeout=20).run()
    assert not at.exception
    button(at,"학생으로 체험하기").click().run()
    assert not at.exception
    next(t for t in at.text_area if t.label=="왜 그렇게 생각했나요?").input("중심이 (2, 1)인 것 같아요.")
    button(at,"설명 제출하기").click().run()
    assert not at.exception
    next(t for t in at.text_area if t.label=="왜 그렇게 생각했나요?").input("y+1=y-(-1)이므로 중심은 (2,-1)이고 반지름은 3입니다.")
    button(at,"설명 제출하기").click().run()
    assert not at.exception
    button(at,"교사로 전환").click().run()
    at.radio[0].set_value("제출·분석").run()
    assert not at.exception
    assert any(m.value=="1 / 1" for m in at.metric)
    next(t for t in at.text_area if t.label=="학생에게 전할 말").input("부호의 이유를 설명하면서 생각이 달라졌어요.")
    button(at,"교사 피드백 저장").click().run()
    assert not at.exception
    button(at,"학생으로 전환").click().run()
    assert not at.exception
    assert any("선생님이 확인했어요" in s.value for s in at.success)
    assert any("부호의 이유" in m.value for m in at.markdown)


def test_teacher_creates_assigns_and_student_sees_task():
    at=AppTest.from_file(APP,default_timeout=20).run()
    button(at,"교사로 체험하기").click().run()
    at.radio[0].set_value("새 과제 만들기").run()
    at.text_input[0].input("두 직선의 평행")
    next(x for x in at.text_area if x.label=="학생에게 제시할 문제").input("y=2x+1과 평행한 직선 하나를 적고 설명하세요.")
    next(x for x in at.text_area if x.label=="교사용 모범 풀이·핵심 개념").input("기울기는 2이고 y절편은 1과 다르다.")
    button(at,"과제 만들고 배정하기").click().run()
    assert not at.exception
    button(at,"학생으로 전환").click().run()
    assert not at.exception
    task=next(x for x in at.selectbox if x.label=="오늘의 과제")
    assert "두 직선의 평행" in task.options
