"""Log Analyzer — captures logs into a ring buffer and exposes analysis tools.

The LogRingBuffer is a loguru sink that stores structured log entries in a
bounded deque. The LogAnalyzer plugin registers LLM tools so the user can
ask GlaDOS to check for errors and save structured reports for debugging.
"""
import json
import os
from collections import defaultdict, deque
from datetime import datetime

from loguru import logger

from glados.context.activity import Activity
from glados.mcp.runnable_mcp_plugin import RunnableMCPPlugin


class LogRingBuffer:
    """Thread-safe ring buffer for loguru log entries."""

    def __init__(self, maxlen: int = 5000):
        self._buffer: deque[dict] = deque(maxlen=maxlen)

    def sink(self, message):
        """Loguru sink callable — receives a loguru Message object."""
        record = message.record
        self._buffer.append({
            "time": record["time"].strftime("%H:%M:%S.%f")[:-3],
            "level": record["level"].name,
            "module": record["module"],
            "function": record["function"],
            "line": record["line"],
            "message": record["message"],
        })

    def get_recent(self, count: int = 100, level: list[str] | str | None = None) -> list[dict]:
        """Get recent entries, optionally filtered by level."""
        entries = list(self._buffer)
        if level:
            levels = level if isinstance(level, list) else [level]
            entries = [e for e in entries if e["level"] in levels]
        return entries[-count:]

    def get_errors(self, count: int = 50) -> list[dict]:
        return self.get_recent(count=count, level=["ERROR", "CRITICAL"])

    def get_warnings_and_errors(self, count: int = 100) -> list[dict]:
        return self.get_recent(count=count, level=["WARNING", "ERROR", "CRITICAL"])

    @property
    def size(self) -> int:
        return len(self._buffer)


# Module-level singleton so main.py can create it before plugins load
_shared_buffer: LogRingBuffer | None = None


def get_shared_buffer() -> LogRingBuffer | None:
    return _shared_buffer


def set_shared_buffer(buf: LogRingBuffer):
    global _shared_buffer
    _shared_buffer = buf


# ---------------------------------------------------------------------------
# NLP response formatters
# ---------------------------------------------------------------------------

def _analyze_nlp_extract(text: str) -> dict:
    text_lower = text.lower()
    if "warning" in text_lower:
        return {"level": "warning"}
    if "all" in text_lower or "everything" in text_lower:
        return {"level": "all"}
    return {"level": "error"}


def _save_report_nlp_extract(text: str) -> dict:
    import re
    m = re.search(r"(?:about|for|regarding|describing)\s+(.+?)\.?$", text, re.IGNORECASE)
    return {"description": m.group(1).strip() if m else ""}


def _analyze_nlp_response(result: dict) -> str:
    if result.get("status") == "clean":
        return "All clear. No errors or warnings in the recent logs."
    errors = result.get("error_count", 0)
    warnings = result.get("warning_count", 0)
    parts = []
    if errors:
        parts.append(f"{errors} error{'s' if errors != 1 else ''}")
    if warnings:
        parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
    return f"Found {' and '.join(parts)} in recent logs."


def _save_report_nlp_response(result: dict) -> str:
    if result.get("status") == "saved":
        errors = result.get("errors", 0)
        return f"Log report saved with {errors} error{'s' if errors != 1 else ''}."
    return result.get("message", "Failed to save report.")


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class LogAnalyzer(RunnableMCPPlugin):

    def __init__(self):
        super().__init__()
        self._data_dir = os.path.join("plugin_data", "log_analyzer")
        os.makedirs(self._data_dir, exist_ok=True)
        self._buffer: LogRingBuffer | None = None

    def start(self):
        # Pick up the shared buffer set by main.py
        self._buffer = get_shared_buffer()
        # Resize buffer if config specifies a different size
        buffer_size = self.plugin_config.get("buffer_size", 5000)
        if self._buffer and self._buffer._buffer.maxlen != buffer_size:
            self._buffer._buffer = deque(self._buffer._buffer, maxlen=buffer_size)
        if not self._buffer:
            logger.warning("[LogAnalyzer] No shared log buffer — tools will be unavailable")
            return

        self.register_tool(
            handler=self.analyze_logs,
            description=(
                "Analyze recent application logs for errors and warnings. "
                "Use when the user asks about errors, issues, or wants to check system health."
            ),
            parameters={
                "level": {
                    "type": "string",
                    "description": "Filter level: 'error' (errors only), 'warning' (warnings + errors), or 'all'",
                    "enum": ["error", "warning", "all"],
                },
            },
            intents=[
                "check the logs",
                "any errors",
                "analyze the logs",
                "what went wrong",
                "are there any errors",
                "check for errors",
                "show me the errors",
                "system health",
                "log status",
                "what errors happened",
            ],
            process_output=True,
            activity=[Activity.SYSTEM, Activity.GENERAL],
            nlp_extract_fn=_analyze_nlp_extract,
            nlp_response=_analyze_nlp_response,
        )

        self.register_tool(
            handler=self.save_log_report,
            description=(
                "Save a structured log report (JSON) with recent errors, warnings, and context. "
                "Use when the user wants to save logs for later analysis or bug reporting."
            ),
            parameters={
                "description": {
                    "type": "string",
                    "description": "Brief description of the issue being reported",
                },
            },
            intents=[
                "save a log report",
                "dump the error logs",
                "save logs for debugging",
                "export the logs",
                "save error report",
            ],
            process_output=True,
            activity=[Activity.SYSTEM, Activity.GENERAL],
            nlp_extract_fn=_save_report_nlp_extract,
            nlp_response=_save_report_nlp_response,
        )

        logger.success(f"[LogAnalyzer] Active — buffer size {self._buffer.size}, saving to {self._data_dir}")

    def stop(self):
        pass

    # ---------------------------------------------------------------------------
    # Tools
    # ---------------------------------------------------------------------------

    def analyze_logs(self, level: str = "error") -> dict:
        """Get recent log entries for LLM analysis."""
        if not self._buffer:
            return {"status": "error", "message": "Log buffer not available."}

        if level == "error":
            entries = self._buffer.get_errors(50)
        elif level == "warning":
            entries = self._buffer.get_warnings_and_errors(100)
        else:
            entries = self._buffer.get_recent(100)

        if not entries:
            return {"status": "clean", "message": "No errors or warnings in recent logs."}

        # Group by module for readable summary
        by_module = defaultdict(list)
        for e in entries:
            by_module[e["module"]].append(e)

        summary_lines = []
        for module, logs in by_module.items():
            summary_lines.append(f"[{module}] {len(logs)} issue(s):")
            for log in logs[-5:]:
                summary_lines.append(
                    f"  {log['time']} {log['level']} {log['function']}:{log['line']} — {log['message'][:200]}"
                )

        return {
            "status": "issues_found",
            "error_count": len([e for e in entries if e["level"] in ("ERROR", "CRITICAL")]),
            "warning_count": len([e for e in entries if e["level"] == "WARNING"]),
            "summary": "\n".join(summary_lines),
            "total_buffered": self._buffer.size,
        }

    def save_log_report(self, description: str = "") -> dict:
        """Save a structured JSON log report to disk."""
        if not self._buffer:
            return {"status": "error", "message": "Log buffer not available."}

        errors = self._buffer.get_errors(50)
        warnings = self._buffer.get_warnings_and_errors(100)
        all_recent = self._buffer.get_recent(200)

        report = {
            "timestamp": datetime.now().isoformat(),
            "description": description,
            "errors": errors,
            "warnings": [w for w in warnings if w["level"] == "WARNING"],
            "context": all_recent,
            "stats": {
                "total_buffered": self._buffer.size,
                "error_count": len(errors),
                "warning_count": len([w for w in warnings if w["level"] == "WARNING"]),
            },
        }

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self._data_dir, f"report_{ts}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"[LogAnalyzer] Report saved to {path} ({len(errors)} errors)")
        return {"status": "saved", "path": path, "errors": len(errors)}
