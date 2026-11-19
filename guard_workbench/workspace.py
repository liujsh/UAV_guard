"""Single-user desktop account and project storage."""

from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from pathlib import Path
from secrets import token_bytes
import sqlite3
from uuid import uuid4

from .scene import SceneConfig, decode_scene, encode_scene


_SCHEMA_VERSION = 1
_PASSWORD_ROUNDS = 600_000


def _label(value: str, field: str, maximum: int) -> str:
    value = value.strip()
    if not 1 <= len(value) <= maximum or not value.isprintable():
        raise ValueError(f"{field}必须包含 1–{maximum} 个可显示字符")
    return value


def _password_digest(password: str, salt: bytes, rounds: int) -> bytes:
    return pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)


@dataclass(frozen=True, slots=True)
class ProjectSummary:
    project_id: str
    name: str


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    name: str
    scene: SceneConfig


class Workspace:
    """Persist projects in one local database and keep sign-in state in memory."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path.resolve()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._username: str | None = None
        with self._connection() as database:
            database.execute("BEGIN IMMEDIATE")
            version = database.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                database.execute("""
                    CREATE TABLE account (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        username TEXT NOT NULL,
                        salt BLOB NOT NULL,
                        rounds INTEGER NOT NULL,
                        password_digest BLOB NOT NULL
                    )
                """)
                database.execute("""
                    CREATE TABLE project (
                        id TEXT PRIMARY KEY NOT NULL,
                        name TEXT NOT NULL,
                        scene_json TEXT NOT NULL
                    )
                """)
                database.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            elif version != _SCHEMA_VERSION:
                raise ValueError(f"不支持的工作区数据库版本：{version}")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._database_path)) as database, database:
            database.row_factory = sqlite3.Row
            yield database

    @property
    def username(self) -> str | None:
        """Return the current account name, or None when signed out."""
        return self._username

    def has_account(self) -> bool:
        with self._connection() as database:
            return database.execute("SELECT 1 FROM account WHERE id = 1").fetchone() is not None

    def set_up_account(self, username: str, password: str) -> None:
        """Create the single local account and sign in on success."""
        username = _label(username, "用户名", 40)
        if not 8 <= len(password) <= 128:
            raise ValueError("密码长度必须为 8–128 个字符")
        salt = token_bytes(16)
        digest = _password_digest(password, salt, _PASSWORD_ROUNDS)
        with self._connection() as database:
            result = database.execute("""
                INSERT INTO account (id, username, salt, rounds, password_digest)
                VALUES (1, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING
            """, (username, salt, _PASSWORD_ROUNDS, digest))
            if result.rowcount != 1:
                raise ValueError("工作区已设置本地账号")
        self._username = username

    def sign_in(self, username: str, password: str) -> bool:
        self._username = None
        with self._connection() as database:
            account = database.execute(
                "SELECT username, salt, rounds, password_digest FROM account WHERE id = 1"
            ).fetchone()
        if account is None or account["username"] != username.strip() or not 8 <= len(password) <= 128:
            return False
        digest = _password_digest(password, account["salt"], account["rounds"])
        if not compare_digest(digest, account["password_digest"]):
            return False
        self._username = account["username"]
        return True

    def sign_out(self) -> None:
        self._username = None

    def _require_sign_in(self) -> None:
        if self._username is None:
            raise PermissionError("请先登录工作区")

    def create_project(self, name: str, scene: SceneConfig | None = None) -> Project:
        self._require_sign_in()
        name = _label(name, "项目名称", 100)
        project = Project(uuid4().hex, name, scene if scene is not None else SceneConfig())
        with self._connection() as database:
            database.execute(
                "INSERT INTO project (id, name, scene_json) VALUES (?, ?, ?)",
                (project.project_id, project.name, encode_scene(project.scene)),
            )
        return project

    def list_projects(self) -> list[ProjectSummary]:
        self._require_sign_in()
        with self._connection() as database:
            rows = database.execute("SELECT id, name FROM project ORDER BY name, id").fetchall()
        return [ProjectSummary(row["id"], row["name"]) for row in rows]

    def open_project(self, project_id: str) -> Project:
        self._require_sign_in()
        with self._connection() as database:
            row = database.execute(
                "SELECT name, scene_json FROM project WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise KeyError("项目不存在")
        return Project(project_id, row["name"], decode_scene(row["scene_json"]))

    def rename_project(self, project_id: str, name: str) -> None:
        self._require_sign_in()
        name = _label(name, "项目名称", 100)
        with self._connection() as database:
            result = database.execute("UPDATE project SET name = ? WHERE id = ?", (name, project_id))
            if result.rowcount != 1:
                raise KeyError("项目不存在")

    def save_project_scene(self, project_id: str, scene: SceneConfig) -> None:
        self._require_sign_in()
        with self._connection() as database:
            result = database.execute(
                "UPDATE project SET scene_json = ? WHERE id = ?", (encode_scene(scene), project_id)
            )
            if result.rowcount != 1:
                raise KeyError("项目不存在")
