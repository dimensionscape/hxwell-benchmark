import os
from pathlib import Path
import time


def parse_stat(text):
    """Parse Linux /proc/pid/stat without splitting spaces in the process name."""
    pid = int(text.split(" ", 1)[0])
    fields = text[text.rfind(")") + 2:].split()
    return {"pid": pid, "group": int(fields[2]), "cpu_ticks": int(fields[11]) + int(fields[12]),
            "threads": int(fields[17]), "start": int(fields[19]), "rss_pages": int(fields[21])}


def snapshot(group):
    rows = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            row = parse_stat(path.read_text())
            if row["group"] == group:
                rows[(row["pid"], row["start"])] = row
        except (OSError, ValueError, IndexError):
            continue
    return rows


class ProcessMeter:
    def __init__(self, group, existing=True):
        self.available = Path("/proc/self/stat").exists()
        self.group = group
        self.last = snapshot(group) if self.available and existing else {}
        self.initial = set(self.last)
        self.seen = set(self.last)
        self.ticks = 0
        self.peak_rss = 0
        self.peak_threads = 0
        self.changed = False
        self.last_at = time.monotonic()

    def sample(self):
        if not self.available:
            return
        rows = snapshot(self.group)
        if self.initial and set(rows) != self.initial:
            self.changed = True
        for identity, row in rows.items():
            before = self.last.get(identity, {}).get("cpu_ticks", 0)
            self.ticks += max(0, row["cpu_ticks"] - before)
            self.seen.add(identity)
        self.last = rows
        self.peak_rss = max(self.peak_rss, sum(row["rss_pages"] for row in rows.values()) * os.sysconf("SC_PAGE_SIZE"))
        self.peak_threads = max(self.peak_threads, sum(row["threads"] for row in rows.values()))

    def result(self, seconds):
        if not self.available:
            return {"available": False, "reason": "Process-group metrics require Linux /proc"}
        cpu = self.ticks / os.sysconf("SC_CLK_TCK")
        return {"available": True, "cpu_seconds": cpu, "cpu_cores_used": cpu / seconds if seconds else None,
                "peak_group_rss_bytes": self.peak_rss, "peak_threads": self.peak_threads,
                "processes_seen": len(self.seen), "process_tree_changed": self.changed}
