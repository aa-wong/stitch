from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .bus import CoordinationBus
from .models import HitlQuestion


InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


@dataclass
class HitlManager:
    bus: CoordinationBus
    input_fn: InputFn = input
    output_fn: OutputFn = print

    def publish(self, question: HitlQuestion) -> None:
        self.bus.add_question(question)

    def prompt_pending(self, run_id: str) -> list[HitlQuestion]:
        answered: list[HitlQuestion] = []
        for question in self.bus.list_questions(run_id=run_id, status="pending"):
            self.output_fn("")
            self.output_fn(f"Question {question.id} from {question.agent}:")
            self.output_fn(question.question)
            if question.options:
                self.output_fn("Options: " + ", ".join(question.options))
            answer = self.input_fn("Answer: ").strip()
            if not answer:
                continue
            answered.append(self.bus.answer_question(question.id, answer))
        return answered

    def answers_by_context_key(self, run_id: str) -> dict[str, str]:
        answers: dict[str, str] = {}
        for question in self.bus.list_questions(run_id=run_id, status="answered"):
            key = question.context.get("key")
            if isinstance(key, str) and question.answer is not None:
                answers[key] = question.answer
        return answers

    def as_pipeline_records(self, run_id: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for question in self.bus.list_questions(run_id=run_id, status="answered"):
            records.append(
                {
                    "id": question.id,
                    "agent": question.agent,
                    "question": question.question,
                    "answer": question.answer,
                    "context": question.context,
                }
            )
        return records
