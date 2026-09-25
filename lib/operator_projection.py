"""Read-only profile/ingestion health projection for Cognitive Cockpit."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any

class DPOperatorProjection:
    def __init__(self, *, profile_path: Path | None=None, event_db: Path | None=None) -> None:
        self.profile_path=profile_path; self.event_db=event_db
    def snapshot(self) -> dict[str, Any]:
        profiles=[]
        if self.profile_path and self.profile_path.exists():
            data=json.loads(self.profile_path.read_text())
            docs=data.get("profiles") if isinstance(data,dict) and isinstance(data.get("profiles"),list) else ([data.get("profile")] if isinstance(data,dict) and isinstance(data.get("profile"),dict) else [data])
            for p in docs:
                if isinstance(p,dict): profiles.append({"profile_id":p.get("profile_id"),"profile_hash":p.get("profile_hash"),"status":p.get("lifecycle_status") or p.get("status"),"sharing_scope":p.get("sharing_scope","agent_private"),"revision":p.get("lifecycle_revision"),"provenance":"host_verified"})
        health={"provenance":"host_verified","available":bool(self.event_db and self.event_db.exists())}
        if self.event_db and self.event_db.exists():
            c=sqlite3.connect(self.event_db); health["events"]=c.execute("select count(*) from events").fetchone()[0];c.close()
        return {"profiles":profiles,"health":health}
