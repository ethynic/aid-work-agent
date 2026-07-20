"""人工步骤完成条件的白名单校验与双采样稳定判定。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal


PredicateType = Literal[
    "url_origin_path_matches", "element_present", "element_absent",
    "challenge_iframe_absent", "page_classifier_state",
]


@dataclass(frozen=True, slots=True)
class CompletionPredicate:
    type: PredicateType
    value: str

    def __post_init__(self) -> None:
        if self.type not in {
            "url_origin_path_matches", "element_present", "element_absent",
            "challenge_iframe_absent", "page_classifier_state",
        }:
            raise ValueError("不支持的完成条件")
        if not self.value or len(self.value) > 512 or "?" in self.value:
            raise ValueError("完成条件只能包含脱敏结构值")


@dataclass(frozen=True, slots=True)
class CompletionObservation:
    origin_path: str
    present_elements: frozenset[str] = frozenset()
    challenge_iframe_present: bool = False
    classifier_state: str = ""


class HumanCompletionMonitor:
    def __init__(self, sample_interval: float = 1.0) -> None:
        self.sample_interval = sample_interval

    @staticmethod
    def evaluate(predicate: CompletionPredicate, observation: CompletionObservation) -> bool:
        if predicate.type == "url_origin_path_matches":
            return observation.origin_path == predicate.value
        if predicate.type == "element_present":
            return predicate.value in observation.present_elements
        if predicate.type == "element_absent":
            return predicate.value not in observation.present_elements
        if predicate.type == "challenge_iframe_absent":
            return not observation.challenge_iframe_present
        if predicate.type == "page_classifier_state":
            return observation.classifier_state == predicate.value
        return False

    async def stable(
        self,
        predicates: tuple[CompletionPredicate, ...],
        sampler: Callable[[], Awaitable[CompletionObservation]],
    ) -> tuple[bool, list[str]]:
        if not predicates:
            return False, ["NO_COMPLETION_CONDITION"]
        missing: list[str] = []
        for sample_index in range(2):
            observation = await sampler()
            missing = [p.type for p in predicates if not self.evaluate(p, observation)]
            if missing:
                return False, missing
            if sample_index == 0:
                await asyncio.sleep(self.sample_interval)
        return True, []
