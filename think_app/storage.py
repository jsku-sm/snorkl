"""Encrypted JSON files. GitHub writes use SHA compare-and-swap retries."""
import base64
import copy
import json
import os
from pathlib import Path
import re
import threading
import time
from cryptography.fernet import Fernet, InvalidToken
import requests


class AppError(Exception):
    pass


def blank():
    return {"schema": 1, "users": {}, "classes": {}, "activities": {}, "attempts": {}, "login_failures": {}}


class MemoryStore:
    def __init__(self):
        self.state = blank()
        self.blobs = {}
        self.lock = threading.RLock()
        self.signing_key = os.urandom(32)

    def read(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def transaction(self, change):
        with self.lock:
            state = copy.deepcopy(self.state)
            result = change(state)
            self.state = state
            return copy.deepcopy(result)

    def put_blob(self, name, data):
        with self.lock:
            self.blobs[name] = data

    def get_blob(self, name):
        with self.lock:
            if name not in self.blobs:
                raise AppError("풀이 이미지가 없습니다.")
            return self.blobs[name]


class LocalStore:
    def __init__(self, directory, key):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.fernet = make_cipher(key)
        self.signing_key = key.encode()
        self.lock = threading.RLock()

    def read(self):
        path = self.root / "state.enc"
        with self.lock:
            return decode(self.fernet, path.read_bytes()) if path.exists() else blank()

    def transaction(self, change):
        with self.lock:
            state = self.read()
            result = change(state)
            raw = self.fernet.encrypt(json.dumps(state, ensure_ascii=False).encode())
            temp = self.root / "state.tmp"
            temp.write_bytes(raw)
            temp.replace(self.root / "state.enc")
            return copy.deepcopy(result)

    def put_blob(self, name, data):
        if not re.fullmatch(r"[a-f0-9]{32}", name):
            raise AppError("파일 식별자가 올바르지 않습니다.")
        (self.root / (name + ".enc")).write_bytes(self.fernet.encrypt(data))

    def get_blob(self, name):
        if not re.fullmatch(r"[a-f0-9]{32}", name):
            raise AppError("파일 식별자가 올바르지 않습니다.")
        try:
            return self.fernet.decrypt((self.root / (name + ".enc")).read_bytes())
        except (OSError, InvalidToken):
            raise AppError("이미지를 읽지 못했습니다. 저장소와 암호화키를 확인하세요.") from None


def make_cipher(key):
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise AppError("ENCRYPTION_KEY에 올바른 Fernet 암호화키를 설정하세요.") from None


def decode(cipher, raw):
    try:
        result = json.loads(cipher.decrypt(raw))
        if result.get("schema") != 1:
            raise ValueError()
        return result
    except (InvalidToken, ValueError, KeyError):
        raise AppError("저장 데이터를 열지 못했습니다. 기존 암호화키를 확인하세요. 새 키로 바꾸지 마세요.") from None


class GitHubStore:
    PREFIX = "think-learning-v1"

    def __init__(self, repo, branch, token, key, session=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not token:
            raise AppError("GITHUB_REPO와 GITHUB_TOKEN을 설정하세요.")
        self.fernet = make_cipher(key)
        self.signing_key = key.encode()
        self.repo, self.branch = repo, branch
        self.http = session or requests.Session()
        self.http.headers.update({"Authorization": "Bearer " + token,
                                  "Accept": "application/vnd.github+json",
                                  "X-GitHub-Api-Version": "2022-11-28"})
        self.base = "https://api.github.com/repos/" + repo
        self._read_lock = threading.RLock()
        self._cached_state = None
        self._cache_until = 0
        self._check_private()

    def _request(self, method, url, **kwargs):
        try:
            return self.http.request(method, url, timeout=25, **kwargs)
        except requests.RequestException:
            raise AppError("GitHub 연결이 지연되었습니다. 입력을 유지한 채 다시 시도하세요.") from None

    def _check_private(self):
        response = self._request("GET", self.base)
        if response.status_code != 200:
            raise AppError("GitHub 저장소 접근 실패: 저장소 이름과 토큰 권한을 확인하세요.")
        if not response.json().get("private"):
            raise AppError("학생 기록은 비공개(Private) 저장소에만 저장할 수 있습니다.")

    def _get(self, path):
        r = self._request("GET", self.base + "/contents/" + self.PREFIX + "/" + path,
                          params={"ref": self.branch})
        if r.status_code == 404:
            return None, None
        if r.status_code != 200:
            raise AppError("GitHub 읽기 실패: 브랜치·토큰 권한·요청 제한을 확인하세요.")
        data = r.json()
        if data.get("encoding") == "none":
            # Contents endpoint omits base64 for files larger than 1 MB.
            b = self._request("GET", self.base + "/git/blobs/" + data["sha"])
            if b.status_code != 200:
                raise AppError("큰 데이터 파일을 읽지 못했습니다.")
            return base64.b64decode(b.json()["content"]), data["sha"]
        return base64.b64decode(data["content"]), data["sha"]

    def _put(self, path, raw, sha=None):
        body = {"message": "Update learning record", "content": base64.b64encode(raw).decode(), "branch": self.branch}
        if sha:
            body["sha"] = sha
        return self._request("PUT", self.base + "/contents/" + self.PREFIX + "/" + path, json=body)

    def read(self):
        # All Streamlit sessions share this store. Pointer events from the board
        # must not cause one GitHub GET per student per pen stroke.
        with self._read_lock:
            if self._cached_state is None or time.monotonic() >= self._cache_until:
                raw, _ = self._get("state.enc")
                self._cached_state = blank() if raw is None else decode(self.fernet, raw)
                self._cache_until = time.monotonic() + 3
            return copy.deepcopy(self._cached_state)

    def invalidate(self):
        with self._read_lock:
            self._cache_until = 0

    def transaction(self, change):
        self._check_private()
        for n in range(6):
            raw, sha = self._get("state.enc")
            state = blank() if raw is None else decode(self.fernet, raw)
            result = change(state)
            encrypted = self.fernet.encrypt(json.dumps(state, ensure_ascii=False).encode())
            if len(encrypted) > 12_000_000:
                raise AppError("학습 기록 파일이 커졌습니다. 교사 화면에서 기록을 내보내고 새 운영 저장소를 준비하세요.")
            r = self._put("state.enc", encrypted, sha)
            if r.status_code in (200, 201):
                self.invalidate()
                return copy.deepcopy(result)
            if r.status_code not in (409, 422):
                raise AppError("GitHub 저장 실패: 토큰 쓰기 권한과 요청 제한을 확인하세요.")
            time.sleep(0.15 * (n + 1))
        raise AppError("동시 제출로 저장이 지연되었습니다. 같은 제출 버튼을 다시 눌러주세요.")

    def put_blob(self, name, data):
        self._check_private()
        if not re.fullmatch(r"[a-f0-9]{32}", name):
            raise AppError("파일 식별자가 올바르지 않습니다.")
        r = self._put("images/" + name + ".enc", self.fernet.encrypt(data))
        if r.status_code not in (200, 201):
            raise AppError("풀이 이미지 저장에 실패했습니다. 다시 시도하세요.")

    def get_blob(self, name):
        if not re.fullmatch(r"[a-f0-9]{32}", name):
            raise AppError("파일 식별자가 올바르지 않습니다.")
        raw, _ = self._get("images/" + name + ".enc")
        if raw is None:
            raise AppError("풀이 이미지가 없습니다.")
        try:
            return self.fernet.decrypt(raw)
        except InvalidToken:
            raise AppError("이미지 암호화키가 일치하지 않습니다.") from None
