"""Instance-wide worktree retention policy, independent of repository contents."""

DEFAULTS = {
    "worktree_cleanup_enabled": True,
    "worktree_cleanup_clean_enabled": True,
    "worktree_cleanup_clean_days": 3,
    "worktree_cleanup_changed_enabled": True,
    "worktree_cleanup_changed_days": 30,
    "worktree_cleanup_interval_hours": 1,
    "worktree_cleanup_batch_size": 50,
}


def validate(values: dict) -> None:
    for key, value in values.items():
        if key not in DEFAULTS or value is None:
            continue
        if key.endswith("_enabled"):
            if type(value) is not bool:
                raise ValueError(f"{key} must be a boolean")
        else:
            maximum = 168 if key.endswith("_hours") else 1000 if key.endswith("_batch_size") else 36500
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{key} must be an integer between 1 and {maximum}")
