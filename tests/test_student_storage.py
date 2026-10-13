import base64
import copy
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import uuid
import threading
import wave

import pytest
from cryptography.fernet import Fernet
from PIL import Image

from think_app.classroom import Classroom
from think_app.courses import CourseManager, now
from think_app.media import validate_audio, validated_graph
from think_app.storage import AppError, GitHubStore, LocalStore, MemoryStore, student_submission_path


def setup(store=None, mode="writing"):
    store = store or MemoryStore()
    manager = CourseManager(store)
    course_id = manager.create("수학", "#6948f5", ["1반", "2반"])
    room = Classroom(store)
    activity_id = room.save_activity(course_id, "활동", "직선을 표현하세요", "y=2x+1", ["기울기 설명"],
                                     5, now()[:10], None, mode=mode)
    return room, manager.get(course_id), activity_id


def recording():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0" * 32000)
    return output.getvalue()


def graph_value():
    output = io.BytesIO()
    Image.new("RGB", (600, 400), "white").save(output, "PNG")
    expressions = [{"id": "1", "latex": "y=2x+1"}]
    return {"ready": True, "state": {"version": 11, "expressions": {"list": expressions}},
            "expressions": copy.deepcopy(expressions),
            "image": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()}


def submit(room, aid, request=None, student="10101", class_id="1반", preview=False, **kwargs):
    return room.submit(aid, student, "김학생", class_id, "기울기는 2입니다", [], False,
                       request or uuid.uuid4().hex, preview=preview, **kwargs)


def test_local_student_file_is_encrypted_and_self_contained_after_restart(tmp_path):
    key = Fernet.generate_key().decode()
    room, _, aid = setup(LocalStore(tmp_path, key), "graph")
    audio, graph = recording(), graph_value()
    request = submit(room, aid, graph=graph, audio=audio, transcript="기울기는 2")
    attempt = room.attempts(aid)[0]
    path = tmp_path / attempt["submission_file"]
    assert path.is_file()
    assert "김학생".encode() not in path.read_bytes()
    assert attempt["submission_file"].startswith("student-submissions/")
    assert "10101" not in attempt["submission_file"]
    restarted = LocalStore(tmp_path, key)
    payload = restarted.get_student_submission(attempt)
    assert payload["submission"]["id"] == request
    assert payload["submission"]["graph"]["state"] == graph["state"]
    assert payload["submission"]["transcript"] == "기울기는 2"
    for reference, asset in payload["assets"].items():
        raw = base64.b64decode(asset["base64"])
        assert raw == restarted.get_blob(reference)
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"]
    assert base64.b64decode(payload["assets"][attempt["audio"]]["base64"]) == audio


def test_student_paths_are_isolated_across_course_class_student_and_preview():
    room, course, aid = setup()
    manager = CourseManager(room.store)
    other_course = manager.create("다른 수업", "#6948f5", ["1반"])
    other_activity = room.save_activity(other_course, "다른 활동", "문제", "답", ["설명"], 5, now()[:10], None)
    for activity, student, class_id, preview in [
        (aid, "10101", "1반", False), (aid, "10102", "1반", False),
        (aid, "10101", "2반", False), (aid, "10101", "1반", True),
        (other_activity, "10101", "1반", False),
    ]:
        submit(room, activity, student=student, class_id=class_id, preview=preview)
    attempts = list(room.store.read()["course_attempts"].values())
    folders = [attempt["submission_file"].rsplit("/", 1)[0] for attempt in attempts]
    assert len(set(folders)) == len(folders)
    assert len(room.store.submissions) == 5
    with pytest.raises(AppError, match="제출 식별자"):
        student_submission_path(dict(attempts[0], id="../../student"))


def test_idempotent_requests_cannot_be_reused_for_another_student_or_activity():
    room, course, aid = setup()
    request = uuid.uuid4().hex
    submit(room, aid, request=request)
    with pytest.raises(AppError, match="다른 학생"):
        submit(room, aid, request=request, student="10102")
    other = room.save_activity(course["id"], "다른 활동", "문제", "답", ["설명"], 5, now()[:10], None)
    with pytest.raises(AppError, match="다른 학생"):
        submit(room, other, request=request)
    room.set_status(aid, "보기만 가능")
    assert submit(room, aid, request=request) == request
    assert len(room.attempts(aid)) == 1


class StudentGitHub:
    def __init__(self):
        self.headers = {}
        self.files = {}
        self.shas = {}
        self.writes = []
        self.fail_bundle = False
        self.conflict_bundle = False

    @staticmethod
    def response(status, payload):
        return SimpleNamespace(status_code=status, json=lambda: payload)

    def request(self, method, url, timeout, **kwargs):
        if "/contents/" not in url:
            return self.response(200, {"private": True})
        path = url.split("/contents/", 1)[1]
        if method == "GET":
            if path not in self.files:
                return self.response(404, {})
            return self.response(200, {"content": base64.b64encode(self.files[path]).decode(),
                                       "encoding": "base64", "sha": self.shas[path]})
        body = kwargs["json"]
        self.writes.append(path)
        if "/student-submissions/" in path and self.fail_bundle:
            self.fail_bundle = False
            return self.response(503, {})
        if "/student-submissions/" in path and self.conflict_bundle:
            self.conflict_bundle = False
            return self.response(409, {})
        if body.get("sha") != self.shas.get(path):
            return self.response(409, {})
        raw = base64.b64decode(body["content"])
        self.files[path] = raw
        self.shas[path] = hashlib.sha256(raw).hexdigest()
        return self.response(200, {})


def test_github_bundle_failure_leaves_no_attempt_then_retry_saves_complete_file():
    key, session = Fernet.generate_key().decode(), StudentGitHub()
    store = GitHubStore("owner/repo", "main", "fake-token", key, session=session)
    room, _, aid = setup(store, "graph")
    request, graph, audio = uuid.uuid4().hex, graph_value(), recording()
    session.fail_bundle = True
    with pytest.raises(AppError, match="학생 제출 파일"):
        submit(room, aid, request=request, graph=graph, audio=audio)
    assert not room.attempts(aid)
    assert not any("/student-submissions/" in path for path in session.files)
    before_retry = len(session.writes)
    session.conflict_bundle = True
    assert submit(room, aid, request=request, graph=graph, audio=audio) == request
    attempt = room.attempts(aid)[0]
    retry_writes = session.writes[before_retry:]
    bundle_path = "think-learning-v1/" + attempt["submission_file"]
    assert retry_writes.index(bundle_path) < retry_writes.index("think-learning-v1/state.enc")
    assert retry_writes.count(bundle_path) == 2
    restarted = GitHubStore("owner/repo", "main", "fake-token", key, session=session)
    payload = restarted.get_student_submission(attempt)
    assert payload["submission"] == attempt
    assert base64.b64decode(payload["assets"][attempt["audio"]]["base64"]) == audio
    assert payload["submission"]["graph"]["state"] == graph["state"]
    previous_writes = len(session.writes)
    assert submit(room, aid, request=request, graph=graph, audio=audio) == request
    assert len(session.writes) == previous_writes


def test_desmos_starter_activity_is_idempotent_concurrent_and_respects_other_courses():
    store = MemoryStore()
    manager = CourseManager(store)
    desmos = manager.create("DESMOS 함수 탐구", "#6948f5", ["1반"])
    room, course = Classroom(store), manager.get(desmos)
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: room.ensure_desmos_activity(course), range(8)))
    assert len(set(ids)) == 1
    assert len(room.activities(desmos)) == 1
    default = room.activity(ids[0])
    assert default["mode"] == "graph" and default["max_attempts"] == 5
    assert default["start"] == now()[:10]
    room.set_status(default["id"], "숨김")
    visible = room.save_activity(desmos, "새 그래프", "문제", "답", ["설명"], 5, now()[:10], None, mode="graph")
    assert room.ensure_desmos_activity(course) == visible
    normal = manager.create("퀴즈 수업", "#6948f5", ["1반"])
    assert room.ensure_desmos_activity(manager.get(normal)) is None
    assert not room.activities(normal)


def test_graph_rejects_stale_summary_and_audio_rejects_missing_pcm_frames():
    graph = graph_value()
    graph["expressions"][0]["latex"] = "y=10x"
    with pytest.raises(AppError, match="저장 상태가 다릅니다"):
        validated_graph(graph)
    with pytest.raises(AppError, match="완전히 저장되지"):
        validate_audio(recording()[:-40])


class ConcurrentStudentGitHub(StudentGitHub):
    def __init__(self):
        super().__init__()
        self.barrier = None
        self.lock = threading.RLock()

    def request(self, method, url, timeout, **kwargs):
        with self.lock:
            result = super().request(method, url, timeout, **kwargs)
        if method == "PUT" and "/student-submissions/" in url and result.status_code == 200 and self.barrier:
            self.barrier.wait(timeout=5)
        return result


def test_simultaneous_same_request_cannot_replace_the_accepted_student_file():
    key, session = Fernet.generate_key().decode(), ConcurrentStudentGitHub()
    store = GitHubStore("owner/repo", "main", "fake-token", key, session=session)
    room, _, aid = setup(store)
    other_store = GitHubStore("owner/repo", "main", "fake-token", key, session=session)
    other_room = Classroom(other_store)
    other_store.read()
    request = uuid.uuid4().hex
    session.barrier = threading.Barrier(2)
    def run(item):
        selected_room, text = item
        return selected_room.submit(aid, "10101", "김학생", "1반", text, [], False, request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [(room, "첫 풀이"), (other_room, "동시 제출한 풀이")]))
    assert results == [request, request]
    store.invalidate()
    accepted = room.attempts(aid)[0]
    assert store.get_student_submission(accepted)["submission"] == accepted
    bundles = [path for path in session.files if "/student-submissions/" in path]
    assert len(bundles) == 2
    assert "think-learning-v1/" + accepted["submission_file"] in bundles
