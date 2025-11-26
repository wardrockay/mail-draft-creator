"""
Structured Logging
==================

Cloud Run optimized JSON logging with structured output,
correlation IDs, and proper severity levels.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Optional, TypeVar
from uuid import uuid4

# Context variable for request correlation
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

F = TypeVar("F", bound=Callable[..., Any])


class CloudRunFormatter(logging.Formatter):
    """
    JSON formatter optimized for Google Cloud Run.
    
    Outputs logs in a format that Cloud Logging can parse,
    with proper severity mapping and structured payloads.
    """
    
    # Map Python log levels to Cloud Logging severity
    SEVERITY_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON for Cloud Logging."""
        # Base log entry
        log_entry: dict[str, Any] = {
            "severity": self.SEVERITY_MAP.get(record.levelno, "DEFAULT"),
            "message": record.getMessage(),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "logging.googleapis.com/sourceLocation": {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            },
        }
        
        # Add request correlation ID if available
        request_id = request_id_var.get()
        if request_id:
            log_entry["logging.googleapis.com/trace"] = request_id
            log_entry["request_id"] = request_id
        
        # Add extra fields from record
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Unknown",
                "message": str(record.exc_info[1]) if record.exc_info[1] else "",
                "traceback": traceback.format_exception(*record.exc_info),
            }
        
        return json.dumps(log_entry, default=str, ensure_ascii=False)


class StructuredLogger:
    """
    Structured logger with context support.
    
    Provides a clean API for logging with automatic
    context inclusion and proper Cloud Run formatting.
    
    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Processing draft", draft_id="abc123", status="pending")
        >>> logger.error("Failed to send", error=str(e), recipient="test@example.com")
    """
    
    def __init__(self, name: str, level: int = logging.INFO) -> None:
        """
        Initialize structured logger.
        
        Args:
            name: Logger name (typically __name__)
            level: Minimum logging level
        """
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        
        # Avoid duplicate handlers
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(CloudRunFormatter())
            self._logger.addHandler(handler)
            self._logger.propagate = False
    
    def _log(
        self,
        level: int,
        message: str,
        exc_info: bool = False,
        **kwargs: Any
    ) -> None:
        """Internal logging method with extra fields."""
        extra = {"extra_fields": kwargs} if kwargs else {}
        self._logger.log(level, message, exc_info=exc_info, extra=extra)
    
    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message."""
        self._log(logging.DEBUG, message, **kwargs)
    
    def info(self, message: str, **kwargs: Any) -> None:
        """Log info message."""
        self._log(logging.INFO, message, **kwargs)
    
    def warning(self, message: str, **kwargs: Any) -> None:
        """Log warning message."""
        self._log(logging.WARNING, message, **kwargs)
    
    def warn(self, message: str, **kwargs: Any) -> None:
        """Alias for warning."""
        self.warning(message, **kwargs)
    
    def error(self, message: str, exc_info: bool = False, **kwargs: Any) -> None:
        """Log error message."""
        self._log(logging.ERROR, message, exc_info=exc_info, **kwargs)
    
    def critical(self, message: str, exc_info: bool = False, **kwargs: Any) -> None:
        """Log critical message."""
        self._log(logging.CRITICAL, message, exc_info=exc_info, **kwargs)
    
    def exception(self, message: str, **kwargs: Any) -> None:
        """Log exception with traceback."""
        self._log(logging.ERROR, message, exc_info=True, **kwargs)


def get_logger(name: str) -> StructuredLogger:
    """
    Get a structured logger instance.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        StructuredLogger instance
    """
    return StructuredLogger(name)


def generate_request_id() -> str:
    """Generate a unique request ID."""
    return str(uuid4())


def set_request_id(request_id: Optional[str] = None) -> str:
    """
    Set the request ID for the current context.
    
    Args:
        request_id: Optional request ID. If not provided, generates a new one.
        
    Returns:
        The request ID that was set.
    """
    rid = request_id or generate_request_id()
    request_id_var.set(rid)
    return rid


def get_request_id() -> str:
    """Get the current request ID."""
    return request_id_var.get()


def log_execution_time(logger: Optional[StructuredLogger] = None) -> Callable[[F], F]:
    """
    Decorator to log function execution time.
    
    Args:
        logger: Logger instance. If not provided, creates one based on function module.
        
    Example:
        >>> @log_execution_time()
        ... def slow_function():
        ...     time.sleep(1)
    """
    def decorator(func: F) -> F:
        nonlocal logger
        if logger is None:
            logger = get_logger(func.__module__)
        
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = datetime.utcnow()
            try:
                result = func(*args, **kwargs)
                duration = (datetime.utcnow() - start_time).total_seconds()
                logger.info(
                    f"Function {func.__name__} completed",
                    function=func.__name__,
                    duration_seconds=duration,
                    status="success"
                )
                return result
            except Exception as e:
                duration = (datetime.utcnow() - start_time).total_seconds()
                logger.error(
                    f"Function {func.__name__} failed",
                    function=func.__name__,
                    duration_seconds=duration,
                    status="error",
                    error=str(e)
                )
                raise
        
        return wrapper  # type: ignore
    
    return decorator
