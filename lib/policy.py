"""Guard lifecycle policy constants, isolated from event/receipt mechanics."""
GUARD_STATUS = {"candidate", "experiment", "validated", "canary", "active", "stale", "rolled_back"}
ALLOWED_TRANSITIONS = {
    "candidate": {"experiment"},
    "experiment": {"validated", "rolled_back"},
    "validated": {"canary", "stale"},
    "canary": {"active", "rolled_back"},
    "active": {"stale", "rolled_back"},
    "stale": set(),
    "rolled_back": set(),
}
PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
