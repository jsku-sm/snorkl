import base64
import io
import json
from types import SimpleNamespace
import uuid
import wave
import zipfile

import pytest
from cryptography.fernet import Fernet
from PIL import Image

from think_app.ai import AI, Feedback
from think_app.classroom import Classroom
from think_app.config import Settings
from think_app.courses import CourseManager, now
from think_app.media import activity_mode, validated_graph
from think_app.storage import AppError, LocalStore, MemoryStore


def setup(mode="writing", title="활동", store=None, options=None):
    store = store or MemoryStore()
    room = Classroom(store)
    cid = CourseManager(store).create("수학", "#6948f5", ["1반"])
    aid = room.save_activity(cid, title, "직선을 표현하세요", "y=2x+1", ["기울기 설명"], 5, now()[:10], None,
                             mode=mode, quiz_options=options)
    return room, cid, aid


def wav(seconds=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0" * (32000 * seconds))
    return output.getvalue()


def graph():
    image = io.BytesIO()
    Image.new("RGB", (1000, 620), "white").save(image, "PNG")
    return dict(ready=True, state={"version": 11, "graph": {"viewport": {"xmin": -5, "xmax": 5}},
                                 "expressions": {"list": [{"id": "1", "latex": "y=2x+1"}]}},
                expressions=[{"id": "1", "latex": "y=2x+1"}],
                image="data:image/png;base64," + base64.b64encode(image.getvalue()).decode())


def submit(room, aid, **kwargs):
    return room.submit(aid, "10101", "학생", "1반", kwargs.pop("text", ""), [], kwargs.pop("consent", False),
                       uuid.uuid4().hex, **kwargs)


@pytest.mark.parametrize("title", ["데스모스", "함수 데스모스 연습", "Desmos graph"])
def test_name_selects_desmos_and_old_tasks_infer_mode(title):
    room, _, aid = setup(title=title)
    assert room.activity(aid)["mode"] == "graph"
    assert activity_mode({"title": title}) == "graph"


def test_graph_and_audio_survive_restart_and_export_each_attempt(tmp_path):
    key = Fernet.generate_key().decode()
    room, cid, aid = setup("graph", store=LocalStore(tmp_path, key))
    value, audio = graph(), wav()
    for _ in range(2):
        submit(room, aid, graph=value, audio=audio, transcript="기울기는 2입니다")
    room = Classroom(LocalStore(tmp_path, key))
    attempts = room.attempts(aid)
    assert [a["number"] for a in attempts] == [1, 2]
    for attempt in attempts:
        assert attempt["graph"]["state"] == value["state"]
        assert room.store.get_blob(attempt["audio"]) == audio
        assert Image.open(io.BytesIO(room.store.get_blob(attempt["images"][0]))).size == (1000, 620)
    with zipfile.ZipFile(io.BytesIO(room.export(cid))) as archive:
        for attempt in attempts:
            assert archive.read(f"답안/{attempt['id']}/녹음.wav") == audio
            assert json.loads(archive.read(f"답안/{attempt['id']}/그래프.json"))["state"] == value["state"]
        csv = archive.read("학생결과.csv").decode("utf-8-sig")
        assert "y=2x+1" in csv and "기울기는 2입니다" in csv


def test_unready_graph_invalid_audio_and_blank_quiz_cannot_submit():
    room, _, aid = setup("graph")
    with pytest.raises(AppError, match="그래프"):
        submit(room, aid, graph=dict(graph(), ready=False))
    with pytest.raises(AppError, match="녹음 파일"):
        submit(room, aid, graph=graph(), audio=b"not wav")
    with pytest.raises(AppError, match="2분"):
        submit(room, aid, graph=graph(), audio=wav(126))
    assert not room.attempts(aid)
    value = graph()
    value["expressions"] = []
    with pytest.raises(AppError, match="수식"):
        validated_graph(value)
    room, _, aid = setup("quiz", options=["y=x", "y=2x+1"])
    with pytest.raises(AppError, match="퀴즈 답"):
        submit(room, aid)
    with pytest.raises(AppError, match="선택지"):
        submit(room, aid, quiz_answer="y=3x")
    submit(room, aid, quiz_answer="y=2x+1")
    assert room.attempts(aid)[0]["quiz_answer"] == "y=2x+1"


def test_audio_is_saved_before_transcription_failure_and_retry_uses_transcript():
    room, _, aid = setup()
    attempt_id = submit(room, aid, audio=wav(), consent=True)
    class BrokenAI:
        available = True
        def transcribe(self, raw):
            raise AppError("연결 실패")
    with pytest.raises(AppError, match="연결 실패"):
        room.evaluate(attempt_id, BrokenAI())
    assert room.attempts(aid)[0]["audio"]
    class GoodAI:
        available = True
        def transcribe(self, raw):
            assert raw == wav()
            return "기울기는 2"
        def feedback(self, task, attempt, images):
            assert attempt["transcript"] == "기울기는 2"
            return {"correctness": "correct", "explanation_score": 3}
    room.evaluate(attempt_id, GoodAI())
    assert room.attempts(aid)[0]["transcript"] == "기울기는 2"
    assert room.attempts(aid)[0]["feedback"]


def test_ai_receives_graph_expressions_capture_quiz_and_transcript():
    capture = {}
    def parse(**kwargs):
        capture.update(kwargs)
        return SimpleNamespace(output_parsed=Feedback(correctness="correct", explanation_score=3,
            praise="좋아요", observation="기울기를 설명했어요", hint="절편도 보세요", next_question="기울기가 바뀌면?",
            criteria=[{"criterion": "기울기 설명", "level": "met", "evidence": "기울기 2"}],
            misconceptions=[], needs_teacher_review=False, uncertainty_reason=""))
    ai = AI(Settings(api_key="test", ai_enabled=True), client=SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    room, _, aid = setup("graph")
    attempt_id = submit(room, aid, graph=graph(), transcript="기울기 2", consent=True)
    room.evaluate(attempt_id, ai)
    payload = json.loads(capture["input"][1]["content"][0]["text"])
    assert payload["학생응답"]["그래프수식과표"][0]["latex"] == "y=2x+1"
    assert payload["학생응답"]["학생이확인한음성전사"] == "기울기 2"
    assert capture["input"][1]["content"][1]["type"] == "input_image"
    assert "10101" not in json.dumps(payload)
    room, _, aid = setup("quiz")
    attempt_id = submit(room, aid, quiz_answer="y=2x+1", consent=True)
    room.evaluate(attempt_id, ai)
    payload = json.loads(capture["input"][1]["content"][0]["text"])
    assert payload["학생응답"]["퀴즈답"] == "y=2x+1"


def test_saved_work_zip_preserves_graph_audio_and_explanation_without_submission():
    from think_app.classroom_ui import work_archive
    room, _, aid = setup("graph")
    value, recording = graph(), wav()
    work = work_archive(room.activity(aid), "기울기는 2", "음성 설명", None, value, None, [], recording)
    with zipfile.ZipFile(io.BytesIO(work)) as archive:
        manifest = json.loads(archive.read("작업.json"))
        assert manifest["text"] == "기울기는 2" and manifest["transcript"] == "음성 설명"
        assert json.loads(archive.read("그래프.json"))["state"] == value["state"]
        assert archive.read("녹음.wav") == recording
        assert Image.open(io.BytesIO(archive.read("그래프.jpg"))).size == (1000, 620)
    assert not room.attempts(aid)
