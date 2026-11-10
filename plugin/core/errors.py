class StudioError(Exception):
    """An error reported to the caller as a structured outcome, never a traceback."""

    def __init__(self, outcome: str, message: str, **details):
        super().__init__(message)
        self.outcome = outcome
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        return {"outcome": self.outcome, "error": self.message, **self.details}
