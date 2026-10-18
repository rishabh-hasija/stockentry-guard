"""Application failures callers can present without inspecting adapter exceptions."""

from stockentry_guard.domain.duplicates import DuplicateResult


class ApprovalRequiredError(ValueError):
    pass


class CommandReuseError(ValueError):
    pass


class ProductNotFoundError(ValueError):
    pass


class DuplicateApprovalBlockedError(ValueError):
    def __init__(self, result: DuplicateResult) -> None:
        self.result = result
        super().__init__("Reviewed input conflicts with an existing likely duplicate")


class ReviewedCandidateError(ValueError):
    pass
