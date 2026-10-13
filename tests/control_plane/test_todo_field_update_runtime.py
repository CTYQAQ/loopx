"""The shared typed field owner retains the supported monitor codec contract."""

AT = "2026-09-01T00:00:00Z"


def test_field_planner_covers_the_existing_monitor_codec_fields():
    from loopx.control_plane.effect_runtime import effect_runtime_result
    from loopx.control_plane.todos.contract import TODO_MONITOR_METADATA_FIELDS

    clears = dict.fromkeys(TODO_MONITOR_METADATA_FIELDS)
    plan = effect_runtime_result(
        "todo.field_update.plan",
        {
            "schema_version": "loopx_todo_field_update_request_v0",
            "todo": {"todo_id": "todo_field_test", "status": "open"},
            "intent": {"monitor_metadata": clears},
            "updated_at": AT,
        },
    )
    assert plan["metadata_updates"] == {
        "todo_id": "todo_field_test",
        "status": "open",
        **clears,
    }
