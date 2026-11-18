class WorkflowArchitectError(Exception):
    """Base error with a stable machine-readable code."""

    code = "WORKFLOW_ARCHITECT_ERROR"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InputError(WorkflowArchitectError):
    code = "INVALID_INPUT"


class UnsupportedTaskError(WorkflowArchitectError):
    code = "UNSUPPORTED_TASK"


class MissingCapabilityError(WorkflowArchitectError):
    code = "MISSING_CAPABILITY"


class CompilationError(WorkflowArchitectError):
    code = "COMPILATION_FAILED"
