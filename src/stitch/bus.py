from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import redis

from .json_yaml import read_document, write_document
from .models import HitlQuestion
from .paths import StitchPaths


class CoordinationBus(Protocol):
    def write_blackboard(self, run_id: str, key: str, value: Any) -> None: ...

    def read_blackboard(self, run_id: str) -> dict[str, Any]: ...

    def add_question(self, question: HitlQuestion) -> None: ...

    def list_questions(self, run_id: str | None = None, status: str | None = None) -> list[HitlQuestion]: ...

    def answer_question(self, question_id: str, answer: str) -> HitlQuestion: ...


@dataclass
class LocalBus:
    paths: StitchPaths

    @property
    def _blackboard_dir(self) -> Path:
        return self.paths.stitch_dir / "blackboards"

    def _blackboard_file(self, run_id: str) -> Path:
        return self._blackboard_dir / f"{run_id}.yaml"

    def write_blackboard(self, run_id: str, key: str, value: Any) -> None:
        blackboard = self.read_blackboard(run_id)
        blackboard[key] = value
        write_document(self._blackboard_file(run_id), blackboard)

    def read_blackboard(self, run_id: str) -> dict[str, Any]:
        return read_document(self._blackboard_file(run_id), {})

    def add_question(self, question: HitlQuestion) -> None:
        questions = [item.to_dict() for item in self.list_questions()]
        for idx, existing in enumerate(questions):
            if existing["id"] == question.id:
                questions[idx] = existing | question.to_dict()
                write_document(self.paths.questions_file, {"questions": questions})
                return
        questions.append(question.to_dict())
        write_document(self.paths.questions_file, {"questions": questions})

    def list_questions(self, run_id: str | None = None, status: str | None = None) -> list[HitlQuestion]:
        data = read_document(self.paths.questions_file, {"questions": []})
        questions = [HitlQuestion.from_dict(item) for item in data.get("questions", [])]
        if run_id is not None:
            questions = [question for question in questions if question.run_id == run_id]
        if status is not None:
            questions = [question for question in questions if question.status == status]
        return questions

    def answer_question(self, question_id: str, answer: str) -> HitlQuestion:
        data = read_document(self.paths.questions_file, {"questions": []})
        questions = data.get("questions", [])
        for idx, item in enumerate(questions):
            if item["id"] == question_id:
                updated = item | {"status": "answered", "answer": answer}
                questions[idx] = updated
                write_document(self.paths.questions_file, {"questions": questions})
                return HitlQuestion.from_dict(updated)
        raise KeyError(f"Unknown question id: {question_id}")


class RedisBus:
    def __init__(self, redis_url: str):
        self._redis = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        )

    def write_blackboard(self, run_id: str, key: str, value: Any) -> None:
        self._redis.hset(f"blackboard:{run_id}", key, json.dumps(value, sort_keys=True))

    def read_blackboard(self, run_id: str) -> dict[str, Any]:
        raw = self._redis.hgetall(f"blackboard:{run_id}")
        return {key: json.loads(value) for key, value in raw.items()}

    def add_question(self, question: HitlQuestion) -> None:
        payload = json.dumps(question.to_dict(), sort_keys=True)
        self._redis.xadd("stream:questions", {"id": question.id, "payload": payload})
        self._redis.publish("pubsub:hitl", payload)

    def list_questions(self, run_id: str | None = None, status: str | None = None) -> list[HitlQuestion]:
        entries = self._redis.xrange("stream:questions")
        questions: dict[str, HitlQuestion] = {}
        for _, fields in entries:
            payload = fields.get("payload")
            if payload:
                question = HitlQuestion.from_dict(json.loads(payload))
                questions[question.id] = question
        for _, fields in self._redis.xrange("stream:answers"):
            question_id = fields.get("id")
            if question_id in questions:
                questions[question_id] = HitlQuestion.from_dict(
                    questions[question_id].to_dict() | {"status": "answered", "answer": fields.get("answer")}
                )
        result = list(questions.values())
        if run_id is not None:
            result = [question for question in result if question.run_id == run_id]
        if status is not None:
            result = [question for question in result if question.status == status]
        return result

    def answer_question(self, question_id: str, answer: str) -> HitlQuestion:
        matches = [question for question in self.list_questions() if question.id == question_id]
        if not matches:
            raise KeyError(f"Unknown question id: {question_id}")
        answered = HitlQuestion.from_dict(matches[0].to_dict() | {"status": "answered", "answer": answer})
        self._redis.xadd("stream:answers", {"id": question_id, "answer": answer})
        return answered


def create_bus(paths: StitchPaths, redis_url: str | None = None) -> CoordinationBus:
    if redis_url:
        try:
            bus = RedisBus(redis_url)
            bus.read_blackboard("__healthcheck__")
            return bus
        except Exception:
            return LocalBus(paths)
    return LocalBus(paths)
