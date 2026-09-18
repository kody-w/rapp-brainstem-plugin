class GatewayError(Exception):
    status_code = 500
    code = -32603


class AuthenticationError(GatewayError):
    status_code = 401
    code = -32001


class InvalidRequestError(GatewayError):
    status_code = 400
    code = -32600


class MethodNotFoundError(GatewayError):
    status_code = 404
    code = -32601


class ToolExecutionError(GatewayError):
    status_code = 422
    code = -32002


class RequestDeadlineError(GatewayError):
    status_code = 504
    code = -32003
