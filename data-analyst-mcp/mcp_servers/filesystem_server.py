"""
Filesystem MCP Server
=====================
A pure Python MCP server using FastMCP that exposes filesystem operations
for reading/writing CSVs, plots, and reports. Runs over stdio transport.

Usage:
    python mcp_servers/filesystem_server.py

The server exposes these tools:
    - list_directory: List files in allowed directories
    - read_file: Read text/CSV files
    - write_file: Write content to files
    - read_csv_preview: Get a pandas-style preview of a CSV
    - get_csv_stats: Get summary statistics of a CSV file
"""

import os
import sys
import json
import csv
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# ─── Configuration ───────────────────────────────────────────────────────────
# Allowed directories (sandbox — agent can only access these)
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
ALLOWED_DIRS = [
    PROJECT_ROOT / "data",
    PROJECT_ROOT / "outputs",
]

# Ensure output directory exists
(PROJECT_ROOT / "outputs").mkdir(exist_ok=True)

# Initialize FastMCP server
mcp = FastMCP(
    "filesystem-manager",
    instructions=(
        "A filesystem server for reading/writing data files and outputs. "
        "Files are sandboxed to the data/ and outputs/ directories. "
        "Use read_csv_preview for quick CSV exploration, read_file for full content, "
        "and write_file to save generated reports or data."
    ),
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _resolve_safe_path(filepath: str) -> Path:
    """Resolve a filepath and ensure it's within allowed directories."""
    path = Path(filepath)
    if not path.is_absolute():
        # Check if the input IS a top-level allowed directory name (e.g. "data", "outputs")
        for allowed in ALLOWED_DIRS:
            if path == Path(allowed.name):
                return allowed
        # Otherwise resolve relative to each allowed dir
        for allowed in ALLOWED_DIRS:
            candidate = (allowed / path).resolve()
            if str(candidate).startswith(str(allowed)):
                return candidate
        # Default to data/
        return (ALLOWED_DIRS[0] / path).resolve()

    resolved = path.resolve()
    for allowed in ALLOWED_DIRS:
        if str(resolved).startswith(str(allowed)):
            return resolved

    raise PermissionError(
        f"Access denied: {filepath} is outside allowed directories "
        f"({', '.join(str(d) for d in ALLOWED_DIRS)})"
    )


# ─── Tools ───────────────────────────────────────────────────────────────────

@mcp.tool()
def list_directory(path: str = "data") -> str:
    """List all files in a directory.

    Args:
        path: Relative directory path (e.g., 'data' or 'outputs').
              Defaults to 'data'.

    Returns a JSON array of file info with name, size, and type.
    """
    try:
        dir_path = _resolve_safe_path(path)
        if not dir_path.is_dir():
            return json.dumps({"error": f"'{path}' is not a directory"})

        files = []
        for item in sorted(dir_path.iterdir()):
            info = {
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "size_bytes": item.stat().st_size if item.is_file() else None,
            }
            if item.is_file():
                info["extension"] = item.suffix
            files.append(info)

        return json.dumps({"directory": path, "contents": files, "count": len(files)})
    except PermissionError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def read_file(path: str) -> str:
    """Read the full content of a text file.

    Args:
        path: File path relative to data/ or outputs/ directory
              (e.g., 'sample.csv' or 'outputs/report.md').

    Returns the file content as text. For large files, consider
    using read_csv_preview instead.
    """
    try:
        file_path = _resolve_safe_path(path)
        if not file_path.is_file():
            return json.dumps({"error": f"File not found: {path}"})

        # Size guard — don't read files larger than 1MB
        size = file_path.stat().st_size
        if size > 1_000_000:
            return json.dumps({
                "error": f"File too large ({size} bytes). "
                         "Use read_csv_preview for large CSVs."
            })

        content = file_path.read_text(encoding="utf-8", errors="replace")
        return json.dumps({
            "path": path,
            "size_bytes": size,
            "content": content,
        })
    except PermissionError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write content to a file in the outputs directory.

    Args:
        path: File path relative to outputs/ directory
              (e.g., 'report.md' or 'results.csv').
        content: The text content to write.

    Automatically prefixes with 'outputs/' if not already specified.
    """
    try:
        # Always write to outputs/ for safety
        if not path.startswith("outputs"):
            path = f"outputs/{path}"

        file_path = _resolve_safe_path(path)

        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        file_path.write_text(content, encoding="utf-8")
        return json.dumps({
            "status": "success",
            "path": str(file_path.relative_to(PROJECT_ROOT)),
            "size_bytes": file_path.stat().st_size,
        })
    except PermissionError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def read_csv_preview(path: str, max_rows: int = 10) -> str:
    """Read a CSV file and return a preview with column info.

    Args:
        path: Path to the CSV file (e.g., 'sample.csv').
        max_rows: Maximum number of rows to preview (default: 10).

    Returns column names, dtypes approximation, row count, and preview rows.
    """
    try:
        file_path = _resolve_safe_path(path)
        if not file_path.is_file():
            return json.dumps({"error": f"File not found: {path}"})

        rows = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            total = 0
            for row in reader:
                total += 1
                if len(rows) < max_rows:
                    rows.append(dict(row))

        # Approximate dtypes
        dtypes = {}
        for col in headers:
            sample_vals = [r.get(col, "") for r in rows[:5] if r.get(col, "")]
            if sample_vals:
                try:
                    [float(v) for v in sample_vals]
                    dtypes[col] = "numeric"
                except ValueError:
                    dtypes[col] = "text"
            else:
                dtypes[col] = "unknown"

        return json.dumps({
            "path": path,
            "columns": headers,
            "dtypes": dtypes,
            "total_rows": total,
            "preview_rows": rows,
            "preview_count": len(rows),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_csv_stats(path: str) -> str:
    """Get summary statistics for numeric columns in a CSV file.

    Args:
        path: Path to the CSV file (e.g., 'sample.csv').

    Returns min, max, mean, and count for each numeric column.
    """
    try:
        file_path = _resolve_safe_path(path)
        if not file_path.is_file():
            return json.dumps({"error": f"File not found: {path}"})

        # Read all data
        all_rows = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            for row in reader:
                all_rows.append(row)

        # Identify numeric columns and compute stats
        stats = {}
        for col in headers:
            values = []
            for row in all_rows:
                try:
                    values.append(float(row.get(col, "")))
                except (ValueError, TypeError):
                    pass

            if len(values) > 0:
                values.sort()
                n = len(values)
                stats[col] = {
                    "count": n,
                    "min": values[0],
                    "max": values[-1],
                    "mean": round(sum(values) / n, 4),
                    "median": values[n // 2],
                    "missing": len(all_rows) - n,
                }

        return json.dumps({
            "path": path,
            "total_rows": len(all_rows),
            "numeric_columns": stats,
            "all_columns": headers,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
