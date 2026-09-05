"""سياسات إعادة المحاولة حسب نوع الخطأ، لا محاولة عمياء."""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..core.errors import ErrorClass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_delay: float = 2.0
    factor: float = 2.0
    max_delay: float = 300.0
    jitter: float = 0.2

    def delay_for(self, attempt: int, *, rng: random.Random | None = None) -> float:
        """تأخير تصاعدي مع عشوائية بسيطة لتفادي الازدحام."""
        raw = min(self.base_delay * (self.factor ** max(0, attempt - 1)), self.max_delay)
        spread = raw * self.jitter
        source = rng or random
        return max(0.0, raw + source.uniform(-spread, spread))


DEFAULT_POLICIES: dict[ErrorClass, RetryPolicy] = {
    ErrorClass.TRANSIENT: RetryPolicy(max_attempts=3, base_delay=2.0),
    ErrorClass.RATE_LIMIT: RetryPolicy(max_attempts=4, base_delay=30.0, max_delay=600.0),
    ErrorClass.AUTH: RetryPolicy(max_attempts=2, base_delay=5.0),
    ErrorClass.DATA_QUALITY: RetryPolicy(max_attempts=2, base_delay=10.0),
    ErrorClass.TARGET_NOT_FOUND: RetryPolicy(max_attempts=1),
    ErrorClass.PERMANENT: RetryPolicy(max_attempts=1),
    ErrorClass.INTERNAL: RetryPolicy(max_attempts=1),
}


def policy_for(error_class: ErrorClass, *, max_attempts: int | None = None) -> RetryPolicy:
    policy = DEFAULT_POLICIES.get(error_class, RetryPolicy(max_attempts=1))
    if max_attempts is not None:
        return RetryPolicy(
            max_attempts=max_attempts,
            base_delay=policy.base_delay,
            factor=policy.factor,
            max_delay=policy.max_delay,
            jitter=policy.jitter,
        )
    return policy
