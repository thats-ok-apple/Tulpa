"""Public errors never reflect upstream bodies, URLs with tokens, or credentials."""


class Failure(Exception):
    def __init__(self, code, message, *, retryable=False, status=None):
        super().__init__(message)
        self.code, self.message, self.retryable, self.status = code, message, retryable, status

    def result(self):
        return dict(ok=False, error=dict(code=self.code, message=self.message,
                    retryable=self.retryable, upstream_status=self.status))


def public_error(exc):
    if isinstance(exc, Failure):
        return exc.result()
    # Do not pass repr/str of HTTP exceptions or subprocess stderr to tools.
    return Failure('internal_error', '本次操作未完成，请查看本地诊断状态或重试只读操作。').result()
