# Log Analyzer

Captures all application logs into a ring buffer and exposes them to GlaDOS for analysis. When something goes wrong, ask GlaDOS to check the logs and save a structured report.

## How It Works

A loguru sink captures every log entry (DEBUG and above) into a bounded `collections.deque` ring buffer (default 5000 entries). The LogAnalyzer plugin exposes two tools:

1. **`analyze_logs`** — summarize recent errors and warnings, grouped by module
2. **`save_log_report`** — save a JSON report with errors, warnings, and surrounding context

## Voice Commands

- "Check the logs" / "Any errors?" — get a spoken summary of recent issues
- "What went wrong?" — same as above
- "Save a log report" / "Dump the error logs" — save JSON report to disk

## Saved Reports

Reports are saved to `plugin_data/log_analyzer/report_YYYYMMDD_HHMMSS.json` with this structure:

```json
{
  "timestamp": "2026-03-29T12:34:56",
  "description": "user-provided description",
  "errors": [
    {
      "time": "12:34:56.789",
      "level": "ERROR",
      "module": "chat_client",
      "function": "chat",
      "line": 332,
      "message": "Chat error: ..."
    }
  ],
  "warnings": [...],
  "context": [...],
  "stats": {
    "total_buffered": 2340,
    "error_count": 3,
    "warning_count": 12
  }
}
```

## Configuration

```yaml
plugins:
  - name: log_analyzer
    config:
      buffer_size: 5000   # max log entries in ring buffer
```

The ring buffer is created early in startup (before plugins load) so it captures the full boot sequence. The `buffer_size` can be increased if you want longer history, but each entry is small (~200 bytes).

## Architecture

```
loguru.add(ring_buffer.sink, level="DEBUG")
    |
    v
LogRingBuffer (deque, maxlen=5000)
    |
    +-- analyze_logs() --> LLM summarizes --> TTS speaks
    |
    +-- save_log_report() --> JSON file --> plugin_data/log_analyzer/
```

The buffer is a module-level singleton (`set_shared_buffer()` / `get_shared_buffer()`) created in `main.py` and picked up by the plugin in `start()`.
